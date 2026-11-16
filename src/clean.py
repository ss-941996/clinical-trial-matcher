"""Stream historical trial ZIPs into normalized, traceable Parquet."""
from __future__ import annotations
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from src.common import ROOT, now, sha256, write_json

STRING_FIELDS = ['trial_id','title','summary','detailed_description','eligibility','inclusion','exclusion',
                 'minimum_age','maximum_age','sex','status','source_archive','source_member','raw_xml']
SCHEMA = pa.schema([(f, pa.string()) for f in STRING_FIELDS] + [('conditions',pa.list_(pa.string())),('interventions',pa.list_(pa.string()))])
HEADING = re.compile(r'(?im)^\s*(?:(?P<inclusion>inclusion)|(?P<exclusion>exclusion))\s+criteria\s*[:\-]?\s*')

def normalize(text):
    text = html.unescape(text or '')
    text = re.sub(r'</?(?:p|br|div|li|ul|ol|b|i|strong|em)\b[^>]*>', '\n', text, flags=re.I)
    return '\n'.join(re.sub(r'[ \t]+', ' ', line).strip() for line in text.replace('\r','\n').splitlines() if line.strip())

def sections(text):
    matches = list(HEADING.finditer(text))
    result = {'inclusion':[], 'exclusion':[]}
    for i, match in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        result[match.lastgroup].append(text[match.end():end].strip())
    return {k:'\n'.join(v) for k,v in result.items()}

def parse_trial(raw, archive='', member=''):
    root = ET.fromstring(raw)
    def value(path):
        node = root.find(path)
        return normalize(''.join(node.itertext())) if node is not None else ''
    trial_id = value('./id_info/nct_id')
    if not re.fullmatch(r'NCT\d{8}',trial_id):
        raise ValueError(f'Invalid trial ID: {trial_id!r} in {member}')
    eligibility = value('./eligibility/criteria')
    return dict(trial_id=trial_id, title=value('./official_title') or value('./brief_title'),
        summary=value('./brief_summary'), detailed_description=value('./detailed_description'),
        eligibility=eligibility, **sections(eligibility), minimum_age=value('./eligibility/minimum_age'),
        maximum_age=value('./eligibility/maximum_age'), sex=value('./eligibility/gender'), status=value('./overall_status'),
        conditions=[normalize(''.join(n.itertext())) for n in root.findall('./condition')],
        interventions=[normalize(''.join(n.itertext())) for n in root.findall('./intervention/intervention_name')],
        source_archive=archive, source_member=member, raw_xml=raw.decode('utf-8-sig'))

def load_topics(path):
    topics={}
    for node in ET.parse(path).getroot().findall('topic'):
        key=node.attrib['number']
        if key in topics: raise ValueError(f'Duplicate topic {key}')
        topics[key]=normalize(''.join(node.itertext()))
    if not topics: raise ValueError('Empty topic file')
    return topics

def load_qrels(path):
    qrels={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        topic, _, trial, label = line.split()
        label=int(label)
        if label not in {0,1,2}: raise ValueError('Unexpected judgment label')
        if trial in qrels.setdefault(topic,{}): raise ValueError(f'Duplicate judgment {topic}/{trial}')
        qrels[topic][trial]=label
    return qrels

def clean(input_dir, output, report_dir, resume_from=None):
    started=time.perf_counter()
    archives=sorted(Path(input_dir).glob('ClinicalTrials.2021-04-27.part[1-5].zip'))
    if len(archives)!=5: raise ValueError('All five official ZIP files are required for a full-corpus build')
    output, report_dir=Path(output),Path(report_dir)
    output.parent.mkdir(parents=True,exist_ok=True)
    report_dir.mkdir(parents=True,exist_ok=True)
    manifest=ROOT/'data/manifests/trec2021.json'
    manifest_data=json.loads(manifest.read_text(encoding='utf-8-sig'))
    receipts={Path(r['file'].replace('\\','/')).name:r for r in manifest_data['files']}
    fingerprints={}
    for archive in archives:
        fingerprints[archive.name]=sha256(archive)
        if archive.name not in receipts or receipts[archive.name]['sha256']!=fingerprints[archive.name]:
            raise ValueError(f'Run src.download to register and verify archive: {archive.name}')
    temp=output.with_suffix('.parquet.partial')
    seen=set(); missing=Counter(); headings=Counter(); statuses=Counter(); abbrevs=Counter(); counts={}
    lengths={f:[] for f in ['title','summary','detailed_description','eligibility']}
    batch=[]
    recovered=0
    def add(record, writer):
        if record['trial_id'] in seen: raise ValueError(f"Duplicate ID: {record['trial_id']}")
        seen.add(record['trial_id'])
        for field in STRING_FIELDS+['conditions','interventions']:
            if not record[field]: missing[field]+=1
        for field in lengths: lengths[field].append(len(record[field]))
        headings['inclusion']+=bool(record['inclusion']); headings['exclusion']+=bool(record['exclusion'])
        headings['both']+=bool(record['inclusion'] and record['exclusion'])
        statuses[record['status']]+=1
        abbrevs.update(re.findall(r'\b[A-Z][A-Z0-9]{1,7}\b',record['summary']+' '+record['eligibility']))
        batch.append(record)
        if len(batch)>=1000:
            writer.write_table(pa.Table.from_pylist(batch,schema=SCHEMA)); batch.clear()
    recovered_sources=set()
    with pq.ParquetWriter(temp,SCHEMA,compression='zstd') as writer:
        if resume_from:
            # Validate cached raw bytes against official archive CRC before reusing any row.
            from contextlib import ExitStack
            with ExitStack() as stack:
                sources={a.name:stack.enter_context(zipfile.ZipFile(a)) for a in archives}
                for chunk in pq.ParquetFile(resume_from).iter_batches(batch_size=1000):
                    for record in chunk.to_pylist():
                        source=(record['source_archive'],record['source_member'])
                        if source in recovered_sources: raise ValueError('Duplicate recovered source')
                        info=sources[source[0]].getinfo(source[1])
                        raw=record['raw_xml'].encode('utf-8')
                        if zlib.crc32(raw)!=info.CRC and zlib.crc32(b'\xef\xbb\xbf'+raw)!=info.CRC:
                            raise ValueError(f'Checkpoint raw XML CRC mismatch: {source}')
                        add(record,writer); recovered_sources.add(source); recovered+=1
                    if recovered%50000==0: print(f'CRC-verified recovered rows: {recovered:,}',flush=True)
        visited_sources=set()
        for archive in archives:
            count=0
            with zipfile.ZipFile(archive) as z:
                for member in z.namelist():
                    if not member.lower().endswith('.xml'): continue
                    source=(archive.name,member)
                    if source in visited_sources: raise ValueError(f'Duplicate ZIP member: {source}')
                    visited_sources.add(source); count+=1
                    if source in recovered_sources: continue
                    try: record=parse_trial(z.read(member),archive.name,member)
                    except Exception as e: raise ValueError(f'Failed {archive.name}/{member}') from e
                    add(record,writer)
                    if len(seen)%25000==0: print(f'Parsed {len(seen):,} trials',flush=True)
            counts[archive.name]=count
        if batch: writer.write_table(pa.Table.from_pylist(batch,schema=SCHEMA))
    if not recovered_sources.issubset(visited_sources): raise ValueError('Recovered rows absent from verified archives')
    # The independently documented corpus count prevents accidental subset claims.
    if len(seen)!=375580: raise ValueError(f'Expected 375580 historical records; parsed {len(seen)}. Inspect before publishing.')
    topics=load_topics(Path(input_dir)/'topics2021.xml')
    qrels=load_qrels(Path(input_dir)/'qrels2021.txt')
    unknown_topics=sorted(set(qrels)-set(topics))
    missing_ids=sorted({d for rows in qrels.values() for d in rows}-seen)
    if unknown_topics or missing_ids: raise ValueError(f'Unmapped judgments: {unknown_topics=}, {len(missing_ids)=}')
    temp.replace(output)
    stats=dict(created_at=now(),recovered_rows=recovered,records=len(seen),archive_records=counts,duplicates=0,parse_failures=0,
        topics=len(topics),judgments=sum(map(len,qrels.values())),judgment_labels=dict(Counter(str(v) for r in qrels.values() for v in r.values())),
        unmapped_judgment_trials=missing_ids,missing_fields=dict(missing),section_detection=dict(headings),
        length_characters={f:dict(zip(['min','p50','p90','p99','max'],[float(x) for x in np.percentile(v,[0,50,90,99,100])])) for f,v in lengths.items()},
        uppercase_token_frequency=dict(abbrevs.most_common(30)),trial_status=dict(statuses),
        source_sha256=fingerprints,parquet_sha256=sha256(output),seconds=time.perf_counter()-started)
    write_json(report_dir/'data_profile.json',stats)
    lines=['# TREC 2021 data profile','',f"Records: {len(seen):,}; patient topics: {len(topics)}; judgments: {stats['judgments']:,}.",
           '',f"Archive counts: `{counts}`.",'','No duplicate IDs, parse failures, or unmapped judged trial IDs. ZIP CRCs checked during member reads.',
           '', '## Missing or empty fields','', '| Field | Count |','|---|---:|']
    lines += [f'| {k} | {v:,} |' for k,v in sorted(missing.items())]
    lines += ['', '## Text lengths (characters)','', '| Field | Median | P90 | P99 | Maximum |','|---|---:|---:|---:|---:|']
    lines += [f"| {f} | {v['p50']:.0f} | {v['p90']:.0f} | {v['p99']:.0f} | {v['max']:.0f} |" for f,v in stats['length_characters'].items()]
    lines += ['', '## Eligibility sections','',f"Detected inclusion headings: {headings['inclusion']:,}; exclusion headings: {headings['exclusion']:,}; both: {headings['both']:,}.",
              'Heading detection is heuristic; undetected prose remains in the complete eligibility field. It is not dropped.',
              '', '## Uppercase token frequency','',str(dict(abbrevs.most_common(30))),
              '','These are abbreviation candidates, not clinically validated expansions.',
              '', '## Normalization and limitations','',
              'Whitespace, HTML entities and common formatting tags are normalized. Raw XML, archive name and member path remain in Parquet.',
              'Age strings and lab units are preserved. No clinical unit conversion or inferred patient facts are performed.',
              'Status is profiled but not filtered: historical benchmark relevance is not a statement of current recruitment.',
              '', 'Sources: [TREC track](https://www.trec-cds.org/2021.html), [NIST labels](https://trec.nist.gov/data/trials2021.html).',
              f"\nProcessing time: {stats['seconds']:.1f} seconds. Full machine-readable statistics and fingerprints: data_profile.json."]
    (report_dir/'data_profile.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    # Development notes are local data artifacts, not source-controlled clinical text.
    write_json(output.parent/'topics2021.json',topics)
    write_json(report_dir/'development_topic_inventory.json',[dict(topic_id=f'2021:{t}',characters=len(topics[t]),judgment_counts=dict(Counter(str(v) for v in qrels[t].values()))) for t in list(sorted(topics,key=int))[:10]])
    print(f'Cleaned {len(seen):,} trials in {stats["seconds"]:.1f}s',flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=ROOT/'data/raw/trec2021')
    p.add_argument('--output',type=Path,default=ROOT/'data/clean/trials.parquet')
    p.add_argument('--reports',type=Path,default=ROOT/'reports')
    p.add_argument('--resume-from',type=Path,help='Completed Parquet batches from an interrupted run; raw XML is CRC-verified against the input archives')
    a=p.parse_args()
    if a.resume_from and a.resume_from.resolve()==a.output.with_suffix('.parquet.partial').resolve(): raise ValueError('Move the checkpoint to a different filename before resuming')
    clean(a.input,a.output,a.reports,a.resume_from)
if __name__=='__main__': main()
