'use strict';
const $=id=>document.getElementById(id);
let responseData=null;
const example='A 54-year-old woman has type 2 diabetes treated with metformin. Her recent HbA1c is 8.1%. She has hypertension treated with lisinopril. Kidney function, smoking history, and prior trial participation are unknown.';
function element(tag,className,text){const e=document.createElement(tag);if(className)e.className=className;if(text!==undefined)e.textContent=text;return e;}
function updateCount(){$('character-count').textContent=`${$('patient-note').value.length.toLocaleString()} / 12,000`;}
$('patient-note').addEventListener('input',updateCount);
$('load-example').addEventListener('click',()=>{$('patient-note').value=example;updateCount();$('patient-note').focus();});
async function loadConfig(){try{const r=await fetch('/config');if(!r.ok)throw Error('Configuration unavailable');const c=await r.json();$('collection-size').textContent=c.indexed_trials.toLocaleString();}catch{$('results-meta').textContent='Could not connect to the service. Check that the API and trial index are ready.';}}
function renderResults(data){
  $('results').replaceChildren();$('empty-state').hidden=true;
  $('results-count').textContent=`${data.results.length} results`;$('results-count').hidden=false;
  $('results-meta').textContent=`${data.candidate_count} candidates · ${data.seconds.toFixed(1)} seconds · eligibility unchecked`;
  if(!data.results.length){$('results').append(element('p','helper','No matching candidates were found. Try adding the primary diagnosis or a more specific description.'));return;}
  for(const trial of data.results){
    const card=element('article','trial-card');const top=element('div','trial-topline');top.append(element('span','rank-number',String(trial.rank).padStart(2,'0')),element('span','trial-id',trial.trial_id),element('span','eligibility-tag','Eligibility unchecked'));card.append(top,element('h3','',trial.title),element('p','trial-conditions',trial.conditions||'No condition field recorded.'));
    const details=element('details');details.append(element('summary','','Read source eligibility criteria'),element('pre','criteria-text',trial.eligibility_text||'Eligibility text is missing from this historical record.'));card.append(details);
    const actions=element('div','trial-actions');const rankText=data.method==='rerank'?`Retrieval position ${trial.retrieval_rank} → displayed position ${trial.rank}`:'Historical record · April 2021 snapshot';actions.append(element('p','rank-change',rankText));
    if(/^NCT\d{8}$/.test(trial.trial_id)){const link=element('a','registry-link','Open registry ↗');link.href=`https://clinicaltrials.gov/study/${trial.trial_id}`;link.target='_blank';link.rel='noopener noreferrer';actions.append(link);}card.append(actions);$('results').append(card);
  }
  $('download-results').hidden=false;
}
$('match-form').addEventListener('submit',async event=>{
  event.preventDefault();const note=$('patient-note').value.trim();if(note.length<20){$('error-message').textContent='Enter at least 20 characters describing the research patient.';$('error-message').hidden=false;return;}
  let elapsed=0;$('search-button').disabled=true;$('patient-note').disabled=true;$('load-example').disabled=true;$('button-label').textContent='Searching the collection…';$('error-message').hidden=true;$('search-progress').hidden=false;$('search-progress').textContent='Preparing trial candidates…';document.querySelector('.results-panel').setAttribute('aria-busy','true');
  // A new request clears the previous response so stale results cannot be mistaken for the current note.
  responseData=null;$('results').replaceChildren();$('download-results').hidden=true;$('results-count').hidden=true;$('empty-state').hidden=false;$('results-meta').textContent='Search in progress…';
  const timer=setInterval(()=>{elapsed++;$('search-progress').textContent=`Searching trial text · ${elapsed}s elapsed`;},1000);
  try{const r=await fetch('/match',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note,top_k:10})});let data;try{data=await r.json();}catch{throw Error('The service returned an unreadable response. Please retry.');}if(!r.ok){const message=typeof data.detail==='string'?data.detail:'The request could not be processed. Check the note and retry.';throw Error(message);}responseData=data;renderResults(data);}
  catch(error){$('error-message').textContent=error.message||'Could not reach the search service.';$('error-message').hidden=false;$('results-meta').textContent='Search did not complete. No results are shown.';}
  finally{clearInterval(timer);$('search-button').disabled=false;$('patient-note').disabled=false;$('load-example').disabled=false;$('button-label').textContent='Find trial candidates';$('search-progress').hidden=true;document.querySelector('.results-panel').setAttribute('aria-busy','false');}
});
$('download-results').addEventListener('click',()=>{if(!responseData)return;const blob=new Blob([JSON.stringify(responseData,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='trial-candidates.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
loadConfig();
