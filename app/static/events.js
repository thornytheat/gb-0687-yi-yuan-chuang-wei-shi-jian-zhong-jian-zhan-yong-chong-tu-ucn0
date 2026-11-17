const bedState={busy:false};const note=document.querySelector('#notice');const replay=document.querySelector('#replay-form');
replay?.addEventListener('submit',async e=>{e.preventDefault();if(bedState.busy)return;bedState.busy=true;const b=replay.querySelector('button');b.disabled=true;b.textContent='回放中';try{const params={observation_end:replay.elements.observation_end.value,late_minutes:replay.elements.late_minutes.value};let options;if(replay.elements.file.files[0]){const f=new FormData();f.append('file',replay.elements.file.files[0]);f.append('params',JSON.stringify(params));options={method:'POST',body:f};}else options={method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:replay.elements.text.value,params})};const res=await fetch('/api/runs',options);const data=await res.json();if(!res.ok)throw Error(data.message);location.assign('/m/3?run='+data.manifest.run_id);}catch(err){note.textContent=err.message;}finally{bedState.busy=false;b.disabled=false;b.textContent='回放事件';}});
const review=document.querySelector('#review');review?.addEventListener('submit',async e=>{e.preventDefault();try{const res=await fetch('/api/review/'+review.dataset.run,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:Number(review.dataset.version),decision:review.elements.decision.value})});const data=await res.json();if(!res.ok)throw Error(data.message);location.reload();}catch(err){note.textContent=err.message;}});

const adjudicate=document.querySelector('#adjudicate');
function pairs(text){const out={};for(const line of (text||'').split(/\r?\n/)){const t=line.trim();if(!t)continue;const i=t.indexOf('=');if(i<0)continue;out[t.slice(0,i).trim()]=t.slice(i+1).trim();}return out;}
function lines(text){return (text||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean);}
adjudicate?.addEventListener('submit',async e=>{e.preventDefault();if(bedState.busy)return;bedState.busy=true;const b=adjudicate.querySelector('button');b.disabled=true;b.textContent='复核中';try{
const decisions=pairs(adjudicate.elements.attributions.value);const responsible=pairs(adjudicate.elements.responsible.value);
const attributions={};for(const [id,decision] of Object.entries(decisions))attributions[id]={decision,responsible:responsible[id]||null};
const releases={};for(const id of lines(adjudicate.elements.releases.value))releases[id]=null;
const parent=adjudicate.elements.parent.value.trim()||null;
if(!Object.keys(attributions).length&&!Object.keys(releases).length)throw Error('请填写归属复核或释放封床号');
const res=await fetch('/api/holds/from-run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({parent,releases,attributions,fail_switch:adjudicate.elements.fail_switch.checked})});
const data=await res.json();if(!res.ok)throw Error(data.message||data.code);
if(data.error)note.textContent='切换失败：'+data.error+'；生效容量沿用 '+(data.fallback&&data.fallback.id?data.fallback.id.slice(0,12):'旧版本');
else location.assign('/m/holds');
}catch(err){note.textContent=err.message;}finally{bedState.busy=false;b.disabled=false;b.textContent='复核并释放';}});
