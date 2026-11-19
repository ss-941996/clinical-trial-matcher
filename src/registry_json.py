"""Read a ClinicalTrials.gov JSON array without loading the entire file."""
import json


def studies(path,chunk_size=262144,with_offsets=False):
    decoder=json.JSONDecoder()
    with open(path,encoding='utf-8-sig') as stream:
        buffer='';pos=0;eof=False;base_bytes=0
        def whitespace():
            nonlocal buffer,pos,eof,base_bytes
            while True:
                while pos<len(buffer) and buffer[pos].isspace():pos+=1
                if pos<len(buffer) or eof:return
                base_bytes+=len(buffer.encode('utf-8'));buffer=stream.read(chunk_size);pos=0;eof=not buffer
        whitespace()
        if not buffer or buffer[pos]!='[':raise ValueError('Expected a JSON array of study records')
        pos+=1;first=True
        while True:
            whitespace()
            if pos>=len(buffer):raise ValueError('Truncated study array')
            if buffer[pos]==']':pos+=1;break
            if not first:
                if buffer[pos]!=',':raise ValueError('Missing comma between studies')
                pos+=1;whitespace()
                if pos>=len(buffer) or buffer[pos]==']':raise ValueError('Missing study after comma')
            while True:
                try:record,end=decoder.raw_decode(buffer,pos);break
                except json.JSONDecodeError as exc:
                    if eof:raise ValueError('Malformed or truncated study record') from exc
                    more=stream.read(chunk_size)
                    base_bytes+=len(buffer[:pos].encode('utf-8'));buffer=buffer[pos:]+more;pos=0;eof=not more
            if not isinstance(record,dict):raise ValueError('Each study must be a JSON object')
            pos=end;first=False
            yield (record,base_bytes+len(buffer[:end].encode('utf-8'))) if with_offsets else record
            if pos>chunk_size:
                base_bytes+=len(buffer[:pos].encode('utf-8'));buffer=buffer[pos:];pos=0
        whitespace()
        if pos<len(buffer):raise ValueError('Unexpected content after study array')
