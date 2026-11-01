/* Metric composer: guided creation, editing, formula/filter builders, live preview. */
const M={sel:null,wiz:null,search:'',pv:{},reach:{},timer:null,msg:''};
const NAME_RE=/^(?!.*__)[a-z][a-z0-9_]*[a-z0-9]$/;
const OPS=[['eq','is'],['ne','is not'],['in','is one of'],['contains','contains'],['gt','>'],['gte','≥'],['lt','<'],['lte','≤'],['null','is empty'],['notnull','is not empty']];
const FNS=[['sum','Total (sum)'],['average','Average'],['min','Smallest'],['max','Largest'],['median','Median'],['count_distinct','Count of unique values']];
const PERIODS=[['day','day'],['week','week'],['month','month'],['quarter','quarter'],['year','year']];
const KINDS=[
  {k:'agg',icon:'Σ',title:'Aggregate a column',text:'Total, average, min, max or unique count of a column. Example: revenue = sum of amount.'},
  {k:'count',icon:'#',title:'Count rows',text:'How many records are in a table. Example: orders = rows in orders.'},
  {k:'ratio',icon:'÷',title:'Ratio of two metrics',text:'One metric divided by another. Example: average order value = revenue ÷ orders.'},
  {k:'formula',icon:'ƒ',title:'Formula',text:'Combine metrics with + − × ÷. Example: profit = revenue − cost.'},
  {k:'growth',icon:'↗',title:'Growth vs previous period',text:'Change compared with the prior day, week, month or year.'},
  {k:'running',icon:'∑',title:'Running total',text:'All-time, year-to-date, month-to-date or rolling window of a metric.'},
];

/* ---------- model helpers ---------- */
const metricByName=n=>B.spec.metrics.find(m=>m.name===n);
const dependsOn=m=>m.type==='ratio'?[m.numerator,m.denominator]:m.type==='derived'?(m.inputs||[]).map(x=>x.name):[];
const usedBy=n=>B.spec.metrics.filter(m=>dependsOn(m).includes(n)).map(m=>m.name);
function depth(name,seen=new Set()){const m=metricByName(name);if(!m||seen.has(name))return 0;seen.add(name);const d=dependsOn(m).filter(Boolean);return d.length?1+Math.max(...d.map(n=>depth(n,new Set(seen)))):0}
function measureOf(name){for(const m of B.spec.semantic_models)for(const x of m.measures||[])if(x.name===name)return {model:m,measure:x};return null}
const nameError=(n,self)=>!n?'Give the metric a name.':!NAME_RE.test(n)?'Use at least 2 lowercase letters, digits or single underscores; start with a letter and do not end with an underscore.':(B.spec.metrics.some(m=>m.name===n&&m!==self)?`A metric called "${n}" already exists.`:'');
function uniqueName(base){let n=base,i=2;while(metricByName(n))n=`${base}_${i++}`;return n}
const slug=s=>String(s||'').toLowerCase().replace(/[^a-z0-9_]+/g,'_').replace(/_+/g,'_').replace(/^_+|_+$/g,'').replace(/^(\d)/,'m_$1');
const tableOf=t=>B.tables.find(x=>x.table===t);
const isNumeric=ty=>/INT|DECIMAL|DOUBLE|FLOAT|REAL|NUMERIC|HUGEINT/i.test(ty);

/* ---------- list + page ---------- */
function renderMetrics(){
  const root=$('#view-metrics');
  if(!B.spec.metrics[M.sel])M.sel=B.spec.metrics.length?0:null;
  const q=M.search.toLowerCase();
  const items=B.spec.metrics.map((m,i)=>({m,i})).filter(({m})=>!q||m.name.toLowerCase().includes(q));
  const list=items.map(({m,i})=>{const d=depth(m.name);
    return `<div class="mitem ${M.sel===i&&!M.wiz?'on':''}" data-act="select" data-i="${i}"><b>${esc(m.name)||'(unnamed)'}</b><span class="badge">${esc(KIND_LABEL(m))}</span>${d?`<span class="muted" title="Stacked on other metrics">L${d+1}</span>`:''}</div>`}).join('')||`<p class="muted" style="padding:6px 4px">${q?'No match.':'No metrics yet.'}</p>`;
  root.innerHTML=`<div class="composer"><aside class="mlist">
    <button class="primary" style="width:100%" data-act="wiz-open">+ New metric</button>
    <input type="text" id="msearch" placeholder="Search metrics" value="${esc(M.search)}" style="margin:10px 0">
    ${list}</aside><div class="mmain" id="mmain"></div></div>`;
  $('#msearch').oninput=e=>{M.search=e.target.value;const pos=e.target.selectionStart;renderMetrics();const s=$('#msearch');s.focus();s.setSelectionRange(pos,pos)};
  root.querySelectorAll('[data-act]').forEach(e=>e.onclick=()=>C[e.dataset.act]?.(e.dataset));
  renderMain();
}
const KIND_LABEL=m=>({simple:'aggregate',ratio:'ratio',derived:'formula',cumulative:m.grain_to_date||m.window?'running':'running'})[m.type]||m.type;
function renderMain(){
  const main=$('#mmain');
  if(M.wiz){main.innerHTML=wizardHtml();bindWizard(main);return}
  const m=B.spec.metrics[M.sel];
  if(!m){main.innerHTML=emptyHtml();main.querySelectorAll('[data-act]').forEach(e=>e.onclick=()=>C[e.dataset.act]?.(e.dataset));return}
  main.innerHTML=editorHtml(m,M.sel);bindEditor(main,m,M.sel);
  if(m.type==='simple'||m.type==='cumulative')loadReach(m);
  schedulePreview(0);
}
function emptyHtml(){
  return `<div class="card"><h3>What do you want to measure?</h3><p class="muted" style="margin-top:0">Pick a starting point. You can combine metrics later.</p>
    <div class="tiles">${KINDS.map(k=>`<button class="tile" data-act="wiz-open" data-kind="${k.k}"><span class="ico">${k.icon}</span><b>${k.title}</b><span class="muted">${k.text}</span></button>`).join('')}</div>
    ${B.tables.length?'':'<p class="issue" style="margin-bottom:0">You have no data yet. Add a table on the Data &amp; models tab first.</p>'}</div>`;
}

/* ---------- wizard ---------- */
function wizOpen(kind){M.wiz={kind:kind||null,filters:[],table:B.tables[0]?.table||'',fn:'sum',column:'',name:'',nameEdited:false,description:'',period:'month',mode:'pct',scope:'all',n:'7',unit:'days',base:'',numerator:'',denominator:'',expr:'',err:''};renderMain()}
function wizardHtml(){
  const w=M.wiz;
  if(!w.kind)return `<div class="card"><h3>New metric <span style="flex:1"></span><button class="sm" data-act="wiz-close">Cancel</button></h3><div class="tiles">${KINDS.map(k=>`<button class="tile" data-act="wiz-kind" data-kind="${k.k}"><span class="ico">${k.icon}</span><b>${k.title}</b><span class="muted">${k.text}</span></button>`).join('')}</div></div>`;
  const kind=KINDS.find(k=>k.k===w.kind),metrics=B.spec.metrics.filter(m=>m.name),mopts=metrics.map(m=>[m.name,m.name]);
  let body='';
  const wsel=(f,label,opts,blank,re=true)=>`<label class="f"><span>${label}</span><select data-w="${f}" ${re?'data-re':''}>${opt(opts,w[f],blank)}</select></label>`;
  const winp=(f,label,ph='',type='text',re=false)=>`<label class="f"><span>${label}</span><input type="${type}" data-w="${f}" ${re?'data-re':''} value="${esc(w[f])}" placeholder="${esc(ph)}"></label>`;
  const tbl=tableOf(w.table),cols=(tbl?.columns||[]).filter(c=>w.kind==='agg'&&w.fn!=='count_distinct'?isNumeric(c.type):true);
  if(w.kind==='agg'||w.kind==='count'){
    body=`<div class="grid">${wsel('table','Table',B.tables.map(t=>[t.table,t.table]),'Select a table')}${w.kind==='agg'?wsel('fn','What to calculate',FNS,null):''}${w.kind==='agg'?wsel('column','Column',cols.map(c=>[c.name,`${c.name}  (${c.type})`]),'Select a column'):''}</div>${filtersHtml(w.filters,reachFor(w.table),'wf')}`;
  }else if(w.kind==='ratio'){
    body=`<div class="grid">${wsel('numerator','Top (numerator)',mopts,'Select a metric')}${wsel('denominator','Bottom (denominator)',mopts,'Select a metric')}</div><p class="muted">Result = top ÷ bottom, calculated correctly at any grouping.</p>`;
  }else if(w.kind==='formula'){
    body=`${formulaHtml('fx-w',w.expr,metrics.map(m=>m.name))}<p class="muted">Click a metric to add it to the formula. Metrics used are linked automatically.</p>`;
  }else if(w.kind==='growth'){
    body=`<div class="grid">${wsel('base','Metric',mopts,'Select a metric')}${wsel('period','Compare with previous',PERIODS,null)}${wsel('mode','Show as',[['pct','Percent change'],['abs','Absolute change']],null)}</div>`;
  }else if(w.kind==='running'){
    const simple=metrics.filter(m=>m.type==='simple').map(m=>[m.name,m.name]);
    body=`<div class="grid">${wsel('base','Aggregate metric to accumulate',simple,'Select a metric')}${wsel('scope','Accumulate',[['all','All time'],['ytd','Year to date'],['mtd','Month to date'],['rolling','Rolling window']],null)}${w.scope==='rolling'?`<div class="row">${winp('n','Length','7','number')}${wsel('unit','Unit',[['days','days'],['weeks','weeks'],['months','months']],null,false)}</div>`:''}</div>`;
  }
  return `<div class="card"><h3><span class="ico sm">${kind.icon}</span>${kind.title}<span style="flex:1"></span><button class="sm" data-act="wiz-kind-back">Back</button><button class="sm" data-act="wiz-close">Cancel</button></h3>
    ${body}<div class="grid" style="margin-top:12px">${winp('name','Metric name',suggestName(),'text')}${winp('description','Description (optional)','')}</div>
    <div id="wiz-err" class="issue" style="min-height:20px;margin-top:8px">${esc(w.err)}</div>
    <div class="row" style="margin-top:6px"><button class="primary" data-act="wiz-create" style="flex:0 0 auto">Create metric</button></div></div>`;
}
function suggestName(){
  const w=M.wiz;let s='';
  if(w.kind==='agg'&&w.column)s=({sum:'total',average:'avg',min:'min',max:'max',median:'median',count_distinct:'unique'})[w.fn]+'_'+w.column;
  else if(w.kind==='count'&&w.table)s=slug(w.table.split('.').pop())+'_count';
  else if(w.kind==='ratio'&&w.numerator&&w.denominator)s=`${w.numerator}_per_${w.denominator}`;
  else if(w.kind==='growth'&&w.base)s=`${w.base}_${w.mode==='pct'?'growth':'change'}`;
  else if(w.kind==='running'&&w.base)s=`${w.base}_${({all:'running',ytd:'ytd',mtd:'mtd',rolling:'rolling_'+w.n+w.unit[0]})[w.scope]}`;
  else if(w.kind==='formula')s='my_formula';
  return slug(s);
}
function bindWizard(root){
  root.querySelectorAll('[data-act]').forEach(e=>e.onclick=()=>C[e.dataset.act]?.(e.dataset));
  root.querySelectorAll('[data-w]').forEach(e=>{
    const f=e.dataset.w,h=()=>{M.wiz[f]=e.value;if(f==='name')M.wiz.nameEdited=true;if(e.dataset.re!==undefined){if(!M.wiz.nameEdited)M.wiz.name=suggestName();if(f==='table'){M.wiz.column='';M.wiz.filters.length=0;loadReachForTable(e.value)}M.wiz.err='';renderMain()}};
    e.oninput=h;if(e.tagName==='SELECT')e.onchange=h;
  });
  const nm=root.querySelector('[data-w=name]');if(nm&&!M.wiz.nameEdited){M.wiz.name=suggestName();nm.value=M.wiz.name}
  bindFilters(root,'wf',M.wiz.filters,s=>{if(s)renderMain()});
  bindFormula(root,'fx-w',v=>{M.wiz.expr=v;});
}
function reachFor(table){return M.reach['t:'+table]||[]}
async function loadReachForTable(table){
  if(!table||M.reach['t:'+table])return;
  const existing=B.spec.semantic_models.find(m=>m.table===table);
  let spec=B.spec,model=existing?.name;
  if(!existing){const s=await api('/builder/suggest',{table});if(!s.ok)return;spec={semantic_models:[...B.spec.semantic_models,s.json.semantic_model],metrics:[]};model=s.json.semantic_model.name}
  const r=await api('/builder/reachable',{spec,model});if(r.ok){M.reach['t:'+table]=r.json.dimensions;if(M.wiz&&M.wiz.table===table)renderMain()}
}
const C={
  'wiz-open':({kind})=>{wizOpen(kind||null);if(kind)loadReachForTable(M.wiz.table);renderMetrics()},
  'wiz-close':()=>{M.wiz=null;renderMetrics()},
  'wiz-kind':({kind})=>{M.wiz.kind=kind;M.wiz.name='';M.wiz.nameEdited=false;loadReachForTable(M.wiz.table);renderMain()},
  'wiz-kind-back':()=>{M.wiz.kind=null;renderMain()},
  select:({i})=>{M.wiz=null;M.sel=+i;renderMetrics()},
  async 'wiz-create'(){
    const w=M.wiz,fail=m=>{w.err=m;$('#wiz-err').textContent=m};
    const err=nameError(w.name);if(err)return fail(err);
    const base={name:w.name,description:w.description||''};
    let created=null;
    if(w.kind==='agg'||w.kind==='count'){
      if(!w.table)return fail('Choose a table.');if(w.kind==='agg'&&!w.column)return fail('Choose a column.');
      const r=await api('/builder/quick-metric',{spec:B.spec,request:{table:w.table,agg:w.kind==='count'?'count_rows':w.fn,column:w.column,name:w.name,description:w.description,filter_rows:w.filters.filter(f=>f.dimension)}});
      if(!r.ok)return fail(r.json.error?.message||'Could not create the metric.');
      B.spec=r.json.spec;created=r.json.metric;
    }else{
      let m;
      if(w.kind==='ratio'){if(!w.numerator||!w.denominator)return fail('Pick both metrics.');if(w.numerator===w.denominator)return fail('Pick two different metrics.');m={...base,type:'ratio',numerator:w.numerator,denominator:w.denominator}}
      else if(w.kind==='formula'){if(!w.expr.trim())return fail('Write a formula using your metrics.');const inputs=inputsFor(w.expr,[],w.name);if(!inputs.length)return fail('The formula must use at least one existing metric.');m={...base,type:'derived',expr:w.expr.trim(),inputs}}
      else if(w.kind==='growth'){if(!w.base)return fail('Pick a metric.');const p=slug(w.base)+'_prior';m={...base,type:'derived',expr:w.mode==='pct'?`(${w.base} - ${p}) / ${p}`:`${w.base} - ${p}`,inputs:[{name:w.base,alias:'',offset_window:''},{name:w.base,alias:p,offset_window:`1 ${w.period}`}]}}
      else if(w.kind==='running'){const src=metricByName(w.base);if(!src)return fail('Pick an aggregate metric.');m={...base,type:'cumulative',measure:src.measure,filter:src.filter||'',filter_rows:JSON.parse(JSON.stringify(src.filter_rows||[])),window:w.scope==='rolling'?`${w.n} ${w.unit}`:'',grain_to_date:w.scope==='ytd'?'year':w.scope==='mtd'?'month':''}}
      B.spec.metrics.push(m);created=m.name;
    }
    M.wiz=null;M.sel=B.spec.metrics.findIndex(x=>x.name===created);touch();rerender();
  },
  dup:({i})=>{const c=JSON.parse(JSON.stringify(B.spec.metrics[+i]));c.name=uniqueName(c.name+'_copy');B.spec.metrics.push(c);M.sel=B.spec.metrics.length-1;touch();renderMetrics()},
  del:({i})=>{const m=B.spec.metrics[+i],users=usedBy(m.name);
    if(users.length&&!confirm(`${users.join(', ')} ${users.length>1?'depend':'depends'} on "${m.name}" and will stop working. Delete anyway?`))return;
    B.spec.metrics.splice(+i,1);M.sel=B.spec.metrics.length?Math.min(+i,B.spec.metrics.length-1):null;touch();renderMetrics()},
  goto:({name})=>{M.wiz=null;M.sel=B.spec.metrics.findIndex(m=>m.name===name);renderMetrics()},
  'add-filter':({scope,i})=>{const m=B.spec.metrics[+i];(m.filter_rows??=[]).push({dimension:'',op:'eq',value:''});renderMain()},
  'adv-add-input':({i})=>{(B.spec.metrics[+i].inputs??=[]).push({name:'',alias:'',offset_window:''});touch();renderMain()},
  'adv-rm-input':({i,j})=>{B.spec.metrics[+i].inputs.splice(+j,1);touch();renderMain()},
  'run-preview':()=>schedulePreview(0),
};

/* ---------- shared builders: filters + formulas ---------- */
function filtersHtml(rows,dims,scope){
  const lines=rows.map((r,j)=>{const noVal=r.op==='null'||r.op==='notnull';
    return `<div class="line fil"><select data-f="${scope}" data-j="${j}" data-k="dimension">${opt(dims.map(d=>[d.name,d.label]),r.dimension,'Dimension')}</select><select data-f="${scope}" data-j="${j}" data-k="op">${opt(OPS,r.op)}</select><input data-f="${scope}" data-j="${j}" data-k="value" value="${esc(r.value||'')}" placeholder="${noVal?'':r.op==='in'?'a, b, c':'value'}" ${noVal?'disabled':''}><button class="sm x" data-fx="${scope}" data-j="${j}">✕</button></div>`}).join('');
  return `<h2>Only include rows where <span class="muted">(optional)</span></h2>${lines||'<div class="muted" style="margin-bottom:6px">All rows are included.</div>'}<button class="sm" data-fadd="${scope}">+ Add condition</button>${dims.length?'':'<span class="muted"> (loading dimensions…)</span>'}`;
}
function bindFilters(root,scope,rows,onChange,after){
  root.querySelectorAll(`[data-f="${scope}"]`).forEach(e=>{const h=()=>{rows[+e.dataset.j][e.dataset.k]=e.value;onChange(e.dataset.k==='op');after?.()};e.oninput=h;if(e.tagName==='SELECT')e.onchange=h});
  root.querySelectorAll(`[data-fx="${scope}"]`).forEach(e=>e.onclick=()=>{rows.splice(+e.dataset.j,1);onChange(true);after?.()});
  root.querySelectorAll(`[data-fadd="${scope}"]`).forEach(e=>e.onclick=()=>{rows.push({dimension:'',op:'eq',value:''});onChange(true);after?.()});
}
function formulaHtml(id,expr,names){
  return `<label class="f"><span>Formula</span><textarea id="${id}" rows="2" placeholder="revenue - cost" spellcheck="false">${esc(expr)}</textarea></label>
   <div class="chips" style="margin-top:8px"><span class="muted">Metrics:</span>${names.map(n=>`<button class="chipbtn" data-ins="${id}" data-v="${esc(n)}">${esc(n)}</button>`).join('')||'<span class="muted">none yet</span>'}</div>
   <div class="chips" style="margin-top:6px"><span class="muted">Operators:</span>${['+','-','*','/','(',')','100'].map(o=>`<button class="chipbtn op" data-ins="${id}" data-v="${o}">${o==='*'?'×':o==='/'?'÷':o==='-'?'−':o}</button>`).join('')}</div>`;
}
function bindFormula(root,id,onChange){
  const ta=root.querySelector('#'+id);if(!ta)return;
  ta.oninput=()=>onChange(ta.value);
  root.querySelectorAll(`[data-ins="${id}"]`).forEach(b=>b.onclick=()=>{const s=ta.selectionStart??ta.value.length,e=ta.selectionEnd??s,pad=/[a-z0-9_]/i.test(b.dataset.v)?' ':' ';
    ta.value=ta.value.slice(0,s)+(s&&!/\s$/.test(ta.value.slice(0,s))?' ':'')+b.dataset.v+pad+ta.value.slice(e);ta.focus();const p=ta.value.length;ta.setSelectionRange(Math.min(p,s+b.dataset.v.length+2),Math.min(p,s+b.dataset.v.length+2));onChange(ta.value)});
}
/* inputs that a formula uses: every referenced metric, keeping aliased (offset) inputs */
function inputsFor(expr,existing,self){
  const tokens=new Set((expr.match(/[a-z][a-z0-9_]*/g)||[])),names=new Set(B.spec.metrics.map(m=>m.name).filter(n=>n&&n!==self));
  const keep=existing.filter(x=>x.alias||x.offset_window);const aliases=new Set(keep.map(x=>x.alias).filter(Boolean));
  const out=[...keep];
  for(const t of tokens){if(names.has(t)&&!aliases.has(t)&&!out.some(x=>x.name===t&&!x.alias))out.push({name:t,alias:'',offset_window:''})}
  return out;
}

/* ---------- editor ---------- */
function editorHtml(m,i){
  const P=`metrics.${i}`,others=B.spec.metrics.filter((x,k)=>k!==i&&x.name).map(x=>[x.name,x.name]);
  const info=m.measure?measureOf(m.measure):null,users=usedBy(m.name),deps=[...new Set(dependsOn(m).filter(Boolean))];
  const measures=B.spec.semantic_models.flatMap(sm=>(sm.measures||[]).filter(x=>x.name).map(x=>[x.name,`${x.name}  (${sm.name}: ${x.agg})`]));
  let body='';
  if(m.type==='simple'||m.type==='cumulative'){
    const how=info?`${info.measure.agg}(${info.measure.expr||info.measure.name}) from ${info.model.table}`:'';
    body=`<div class="grid">${sel(P+'.measure','Calculated from',measures,'Select a measure')}</div>${how?`<div class="muted" style="margin:4px 0 8px">= ${esc(how)}</div>`:''}`;
    if(m.type==='cumulative')body+=`<div class="grid">${inp(P+'.window','Rolling window (e.g. 7 days)','')}${sel(P+'.grain_to_date','…or accumulate since start of',GRAINS,'(all time)')}</div>`;
    body+=m.filter&&!(m.filter_rows||[]).length?`<h2>Filter <span class="muted">custom expression</span></h2>${inp(P+'.filter','',"{{ Dimension('order__status') }} = 'paid'")}`:filtersHtml(m.filter_rows||[],M.reach['m:'+m.measure]||[],'ef');
  }else if(m.type==='ratio'){
    body=`<div class="grid">${sel(P+'.numerator','Top (numerator)',others,'Select a metric')}${sel(P+'.denominator','Bottom (denominator)',others,'Select a metric')}</div>`;
  }else{
    body=`${formulaHtml('fx-e',m.expr||'',others.map(o=>o[0]))}
     <details style="margin-top:10px" ${(m.inputs||[]).some(x=>x.alias||x.offset_window)?'open':''}><summary class="muted" style="cursor:pointer">Advanced: compare with an earlier period</summary>
     <p class="muted">Give an input an alias and an offset (e.g. "1 month") to use its earlier value in the formula under that alias.</p>
     ${(m.inputs||[]).map((x,j)=>`<div class="line inp"><select data-p="${P}.inputs.${j}.name">${opt(others,x.name,'Metric')}</select><input data-p="${P}.inputs.${j}.alias" value="${esc(x.alias||'')}" placeholder="alias e.g. prior"><input data-p="${P}.inputs.${j}.offset_window" value="${esc(x.offset_window||'')}" placeholder="offset e.g. 1 month"><button class="sm x" data-act="adv-rm-input" data-i="${i}" data-j="${j}">✕</button></div>`).join('')}
     <button class="sm" data-act="adv-add-input" data-i="${i}">+ Input</button></details>`;
  }
  const chips=(label,names)=>names.length?`<div class="tree">${label}: ${names.map(n=>`<button class="chipbtn" data-act="goto" data-name="${esc(n)}">${esc(n)}</button>`).join(' ')}</div>`:'';
  return `<div class="card"><h3><span style="flex:0 0 auto">${esc(m.name)||'(unnamed)'}</span><span class="badge">${esc(KIND_LABEL(m))}</span>${depth(m.name)?`<span class="badge" title="Stacked on other metrics">level ${depth(m.name)+1}</span>`:''}<span style="flex:1"></span><button class="sm" data-act="dup" data-i="${i}">Duplicate</button><button class="sm x" data-act="del" data-i="${i}">Delete</button></h3>
    <div class="grid"><label class="f"><span>Name</span><input id="mname" value="${esc(m.name)}"><span id="mname-err" class="issue" style="text-transform:none;letter-spacing:0"></span></label>${inp(P+'.description','Description','What this metric means')}</div>
    <div style="height:10px"></div>${body}${chips('Built on',deps)}${chips('Used by',users)}${deps.length?`<details class="tree"><summary style="cursor:pointer">How it is built</summary>${treeHtml(m.name)}</details>`:''}</div>
    <div class="card" id="pvcard"><h3>Preview <span class="muted" id="pvstate"></span><span style="flex:1"></span><label class="f" style="flex:0 0 220px;margin:0"><select id="pvgroup" aria-label="Group by"><option value="">No grouping</option></select></label><button class="sm" data-act="run-preview">Run</button></h3><div id="pvbody" class="muted">Preview runs on your data as you edit.</div></div>`;
}
function treeHtml(name,seen=new Set()){
  const m=metricByName(name);if(!m||seen.has(name))return '';
  const d=dependsOn(m).filter(Boolean),info=m.measure?measureOf(m.measure):null;
  const leaf=info?` <span class="muted">= ${esc(info.measure.agg)}(${esc(info.measure.expr||info.measure.name)}) from ${esc(info.model.table)}</span>`:'';
  return `<ul class="tr"><li><b>${esc(name)}</b> <span class="badge">${esc(KIND_LABEL(m))}</span>${leaf}${d.map(n=>treeHtml(n,new Set([...seen,name]))).join('')}</li></ul>`;
}
function bindEditor(root,m,i){
  root.querySelectorAll('[data-act]').forEach(e=>e.onclick=()=>C[e.dataset.act]?.(e.dataset));
  root.querySelectorAll('[data-p]').forEach(e=>{const h=()=>{setp(B.spec,e.dataset.p,e.value);touch()};e.oninput=h;if(e.tagName==='SELECT')e.onchange=()=>{h();if(/\.(measure|numerator|denominator)$/.test(e.dataset.p))renderMain()}});
  const nm=root.querySelector('#mname');
  nm.onchange=()=>{const v=nm.value.trim(),err=nameError(v,m);$('#mname-err').textContent=err;if(err)return;renameMetric(m.name,v);m.name=v;touch();renderMetrics()};
  bindFilters(root,'ef',m.filter_rows||(m.filter_rows=[]),(structural)=>{if(structural)renderMain();touch()});
  bindFormula(root,'fx-e',v=>{m.expr=v;m.inputs=inputsFor(v,m.inputs||[],m.name);touch()});
  $('#pvgroup').onchange=()=>{const pv=(M.pv[m.name]??={});pv.group=$('#pvgroup').value;pv.userPicked=true;schedulePreview(0)};
}
function renameMetric(oldName,newName){
  const re=new RegExp(`\\b${oldName}\\b`,'g');
  for(const x of B.spec.metrics){
    if(x.numerator===oldName)x.numerator=newName;if(x.denominator===oldName)x.denominator=newName;
    if(x.type==='derived'){let used=false;for(const inp of x.inputs||[])if(inp.name===oldName){inp.name=newName;used=true}
      if(used&&!(x.inputs||[]).some(inp=>inp.alias===oldName))x.expr=(x.expr||'').replace(re,newName)}
  }
  if(M.pv[oldName]){M.pv[newName]=M.pv[oldName];delete M.pv[oldName]}
}
async function loadReach(m){
  if(!m.measure||M.reach['m:'+m.measure])return;
  const r=await api('/builder/reachable',{spec:B.spec,measure:m.measure});
  if(r.ok){M.reach['m:'+m.measure]=r.json.dimensions;if(M.sel!=null&&B.spec.metrics[M.sel]===m&&!M.wiz)renderMain()}
}

/* ---------- live preview ---------- */
/* Metrics that compare periods (offsets) need a metric_time grouping; find the finest grain used. */
function offsetGrain(name,seen=new Set()){
  const m=metricByName(name);if(!m||seen.has(name))return null;seen.add(name);
  const order=['day','week','month','quarter','year'];let best=null;
  const finer=g=>g&&order.includes(g)&&(best===null||order.indexOf(g)<order.indexOf(best));
  for(const inp of (m.type==='derived'?m.inputs||[]:[])){
    const g=(String(inp.offset_window||'').trim().split(/\s+/)[1]||'').replace(/s$/,'');
    if(finer(g))best=g;
  }
  for(const d of dependsOn(m)){const g=offsetGrain(d,seen);if(finer(g))best=g}
  return best;
}
function schedulePreview(delay=600){clearTimeout(M.timer);M.timer=setTimeout(runPreview,delay)}
function onChecked(){if(B.view==='metrics'&&!M.wiz&&B.spec.metrics[M.sel])schedulePreview(150)}
async function runPreview(){
  const m=B.spec.metrics[M.sel];if(!m||M.wiz||B.view!=='metrics')return;
  const body=$('#pvbody');if(!body)return;
  const errs=B.issues.filter(x=>x.level==='error');
  if(errs.length){body.innerHTML=`<span class="issue">Fix this to see a preview: ${esc(errs[0].message)}</span>`;return}
  const pv=(M.pv[m.name]??={}),stamp=pv.stamp=(pv.stamp||0)+1;
  const grain=offsetGrain(m.name);if(grain&&!pv.group&&!pv.userPicked)pv.group='metric_time__'+grain;
  $('#pvstate').textContent='running…';
  const r=await api('/builder/preview',{spec:B.spec,metric:m.name,group_by:pv.group||undefined,limit:50});
  if(pv.stamp!==stamp||B.spec.metrics[M.sel]!==m||!$('#pvbody'))return;
  $('#pvstate').textContent='';
  if(!r.ok){$('#pvbody').innerHTML=`<span class="issue">${esc(r.json.error?.message||'Preview failed')}</span>`;return}
  const {columns,rows,sql,dimensions,error}=r.json,g=$('#pvgroup');
  if(g){g.innerHTML='<option value="">No grouping</option>'+(dimensions||[]).map(d=>`<option value="${esc(d.name)}" ${d.name===pv.group?'selected':''}>${esc(d.name)}</option>`).join('')}
  if(error){$('#pvbody').innerHTML=`<span class="issue" style="white-space:pre-wrap">${esc(error)}</span>`;return}
  const single=columns.length===1&&rows.length===1;
  $('#pvbody').innerHTML=(single?`<div class="kpi">${fmt(rows[0][columns[0]])}</div><div class="muted">${esc(m.name)} across all data</div>`
    :rows.length?`<div class="scroll"><table><thead><tr>${columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(x=>`<tr>${columns.map(c=>`<td class="${typeof x[c]==='number'?'num':''}">${fmt(x[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<span class="muted">No rows.</span>')
    +`<details style="margin-top:8px"><summary class="muted" style="cursor:pointer">Show SQL</summary><pre>${esc(sql)}</pre></details>`;
}
