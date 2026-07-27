const NODE_ORDER=[
  ["init_crawl","Initialize"],["discover_urls","Discover URLs"],["filter_discovered_urls","URL Filter"],
  ["scrape_page","Scrape Pages"],["collect_scraped","Collect"],["content_classification","Classify"],
  ["identify_programs","Target Program"],["structured_extraction","LLM Extract"],["hallucination_validation","Validate"],
  ["semantic_repair","Repair"],["sufficiency_evaluator","Coverage"],["chunking","Chunk"],
  ["db_writer","Write DB"],["finalize_school","Complete"]
];
let state=null,urlTab="keep";
const $=s=>document.querySelector(s), esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const short=(v,n=180)=>{v=String(v??"").replace(/\s+/g," ");return v.length>n?v.slice(0,n)+"…":v};
function latestByNode(events){const out={};events.forEach(e=>out[e.node]=e);return out}
function renderPipeline(events){
  const last=latestByNode(events), completed=NODE_ORDER.filter(([n])=>last[n]?.status==="completed"||last[n]?.status==="skipped").length;
  const pct=Math.round(completed/NODE_ORDER.length*100);
  $("#progressValue").textContent=pct+"%";$("#progressRing").style.setProperty("--progress",pct*3.6+"deg");
  $("#pipeline").innerHTML=NODE_ORDER.map(([id,label])=>{const e=last[id],status=e?.status||"pending";return `<div class="node ${status}"><div class="dot"></div><b>${label}</b><small>${esc(e?.message||"Waiting")}</small></div>`}).join("");
}
function renderMetrics(s){
  const ev=s.events||[],f=s.url_filter?.summary||{},db=s.db?.counts||{},result=s.result||{};
  const vals=[["URLs discovered",result.page_results?.length||s.urls?.all?.length||0],["URLs kept",f.kept??s.urls?.keep?.count??0],["Pages in DB",db.pages||0],["RAG chunks",db.chunks||result.chunk_count||0],["Review items",db.reviews||result.review_items?.length||0]];
  $("#metrics").innerHTML=vals.map(([l,v])=>`<div class="metric"><label>${l}</label><strong>${v}</strong></div>`).join("");
  const finish=[...ev].reverse().find(e=>e.node==="finalize_school"),active=[...ev].reverse()[0];
  $("#runSummary").textContent=finish?"Crawler completed · DB verification ready":active?active.message:"Waiting for crawler events";
}
function urlItems(kind){
  if(kind==="all")return (state.urls?.all||[]).map(url=>({url,decision:"all"}));
  const value=state.urls?.[kind]||{};return value.urls||[];
}
function renderUrls(){
  $("#urlTabs").innerHTML=["keep","drop","all"].map(k=>`<button data-tab="${k}" class="${urlTab===k?"active":""}">${k.toUpperCase()} · ${urlItems(k).length}</button>`).join("");
  $("#urlTabs").querySelectorAll("button").forEach(b=>b.onclick=()=>{urlTab=b.dataset.tab;renderUrls()});
  const items=urlItems(urlTab);$("#urlList").innerHTML=items.length?items.map((x,i)=>`<div class="url-row"><span class="idx">${String(i+1).padStart(2,"0")}</span><a href="${esc(x.url)}" target="_blank">${esc(x.url)}</a><span class="pill ${x.decision==="drop"?"drop":""}">${esc(x.decision||urlTab)}</span>${x.reason?`<p>${esc(short(x.reason,150))}</p>`:""}</div>`).join(""):`<div class="empty">尚無 ${urlTab} URL</div>`;
}
function renderActivity(events){const items=[...events].reverse().slice(0,60);$("#activity").innerHTML=items.length?items.map(e=>`<div class="event"><time>${esc((e.timestamp||"").slice(11,19))}</time><b>${esc(e.node)}</b><p>${esc(e.message)}</p>${e.url?`<p>${esc(short(e.url,90))}</p>`:""}</div>`).join(""):`<div class="empty">等待事件…</div>`}
function pageModels(s){
  const map={};(s.events||[]).forEach(e=>{if(!e.url)return;map[e.url]??={url:e.url};if(e.node==="scrape_page"&&e.data)map[e.url].scrape=e.data;if(e.node==="content_classification")map[e.url].classification=e.data;if(e.node==="structured_extraction")map[e.url].extraction=e.data;if(e.node==="hallucination_validation")map[e.url].validation=e.data;if(e.node==="finalize_page")map[e.url].final=e.data});
  (s.result?.page_results||[]).forEach(p=>{map[p.url]??={url:p.url};map[p.url].result=p});
  return Object.values(map).filter(p=>p.scrape||p.result).slice(0,3)
}
function renderPages(s){const pages=pageModels(s);$("#pageGrid").innerHTML=pages.length?pages.map(p=>{const r=p.result||{},ex=p.final?.extraction||r.extraction||{},fields=p.extraction?.fields||[...new Set((ex.programs||[]).flatMap(x=>Object.keys(x.fields||{})))],validation=p.validation||{},preview=p.scrape?.content_preview||r.data||"";return `<article class="page-card"><div class="page-top"><h4>${esc(short(p.scrape?.title||r.title||"Official page",55))}</h4><span class="pill">${esc(r.status||"live")}</span></div><a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a><div class="preview">${esc(short(preview,430))}</div><div class="fields">${fields.slice(0,12).map(f=>`<span class="field">${esc(f)}</span>`).join("")}</div><div class="validation">${validation.total_fields!=null?`Validated ${validation.total_fields} · ${validation.issues?.length||0} issues`:"Waiting for validation"}</div></article>`}).join(""):`<div class="empty">頁面完成後將顯示於此</div>`}
const DB_FIELDS=[["program_code","Program"],["gpa_min","GPA"],["gpa_note","GPA context"],["toefl_ibt_min","TOEFL old"],["toefl_ibt_new_scale_min","TOEFL new"],["toefl_section_requirements","TOEFL sections"],["ielts_min","IELTS"],["duolingo_min","Duolingo"],["language_waiver","English waiver"],["gre_required","GRE"],["rec_letter_count","Letters"],["cv_required","CV"]];
function renderDb(db){if(!db?.found){$("#dbRecord").innerHTML=`<div class="empty">${db?.connected?"等待 DB 寫入…":"DB unavailable: "+esc(db?.error||"")}</div>`;$("#dbVerification").innerHTML=`<div class="empty">尚無驗證結果</div>`;return}const p=db.program||{};$("#dbRecord").innerHTML=`<div class="kv">${DB_FIELDS.map(([k,l])=>`<div><label>${l}</label><strong>${esc(p[k]??"—")}</strong></div>`).join("")}</div>`;const deadlines=db.deadlines||[],evidence=db.evidence||[],reviews=db.reviews||[];$("#dbVerification").innerHTML=`<div class="verify-body"><div class="verify-card"><b class="status-ok">✓ Database connected & record found</b><p>${db.counts.pages} pages · ${db.counts.chunks} chunks · ${evidence.length} evidence paragraphs</p></div><div class="verify-card ${deadlines.length?"":"warning"}"><b>Deadlines · ${deadlines.length}</b>${deadlines.length?deadlines.map(d=>`<p>${esc(d.semester||"unspecified")} — ${esc(d.application_close_date||d.note||"")}</p>`).join(""):`<p>沒有可安全正規化的日期；請查看 deadline evidence。</p>`}</div><div class="verify-card ${reviews.length?"warning":""}"><b>Review queue · ${reviews.length}</b>${reviews.slice(0,5).map(r=>`<p>${esc(r.field_name)} — ${esc(short(r.source_excerpt,130))}</p>`).join("")||"<p>No unresolved validation items.</p>"}</div><div class="verify-card"><b>Evidence preview</b>${evidence.slice(0,6).map(e=>`<p><strong>${esc(e.category)} / ${esc(e.field_name)}</strong><br>${esc(short(e.evidence_text,160))}</p>`).join("")}</div></div>`}
function render(s){state=s;$("#schoolTitle").textContent=(s.school_id||"school").toUpperCase();renderPipeline(s.events||[]);renderMetrics(s);renderUrls();renderActivity(s.events||[]);renderPages(s);renderDb(s.db);$("#lastUpdate").textContent=new Date().toLocaleTimeString("en-GB")}
async function load(){const school=$("#schoolInput").value.trim()||"gatech";try{const r=await fetch(`/api/state?school_id=${encodeURIComponent(school)}`,{cache:"no-store"});render(await r.json())}catch(e){$("#runSummary").textContent="Dashboard API unavailable: "+e.message}}
const initial=new URLSearchParams(location.search).get("school");if(initial)$("#schoolInput").value=initial;$("#loadButton").onclick=load;$("#schoolInput").onkeydown=e=>{if(e.key==="Enter")load()};load();setInterval(load,2000);
