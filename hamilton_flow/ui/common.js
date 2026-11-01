/* Shared helpers for the Explorer and Builder. */
const $=s=>document.querySelector(s);
const STAGES=['ingest','parse','validate','plan','render','execute','check'];
const GRAINS=['day','week','month','quarter','year'];
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(path,body){
  const r=await fetch(path,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{});
  return {ok:r.ok,status:r.status,json:await r.json()};
}
async function api2(path,method,body){const r=await fetch(path,{method,headers:{'content-type':'application/json'},body:JSON.stringify(body)});return {ok:r.ok,json:await r.json()}}
