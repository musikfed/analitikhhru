(() => {
 "use strict";
 // Archive measurements are copied from the supplied screenshots, not from live hh.ru.
 const $ = id => document.getElementById(id);
 const set = (id,value) => {const e=$(id);if(e)e.textContent=value};
 const fmt = n => new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(n);
 set('statusText','Архивный режим: показатели со снимков; онлайн-сбор отключён');
 set('lastUpdated','Снимки: 30 июля и 31 августа 2026');
 set('browserStatus','Chrome: без подключения');
 set('searchForceStatus','Сценарный proxy · K меняется ниже');
 set('searchForceRecruiters','IT-рекрутеры: 12 открытых вакансий (не число специалистов)');
 const kpis={
  vacancy_active_now:'2 715',vacancy_published_30d:'3 507',
  resume_active_now:'702',resume_active_published_30d:'—',
  competition_stock_average:'0,26',competition_flow_average:'—',
  hr_vacancy_density:'5,53',observed_support_score:'92,9',
  proxy_support_index:'37,6',proxy_deficit_index:'62,4'
 };
 for(const el of document.querySelectorAll('[data-kpi]')){el.textContent=kpis[el.dataset.kpi]||'—';el.classList.remove('skeleton')}
 // The overview is July 30; HR density and proxy are August 31. Avoid merging both dates into a single observation.
 const aside=document.createElement('p');aside.className='snapshot-note';
 aside.textContent='Обзор вакансий и резюме относится к снимку 30.07.2026; блоки HR-плотности, сценарного K и пульса — к снимку 31.08.2026. Числа разных дат нельзя считать одной выборкой.';
 $('kpiGrid')?.after(aside);
 const T=2170,HR=12,density=1000*HR/T;
 const scenario=k=>{const required=T/k,pressure=HR/required,support=100/(1+pressure);return{required,pressure,support}};
 function renderCapacity(){
  const select=$('capacitySelect');const k=Number(select.value)||300;const x=scenario(k),low=scenario(20).support,high=scenario(40).support,baseline=scenario(25).support;
  set('modelTargetVacancies','2 170');set('modelHrDensity',fmt(density));set('modelRequiredRecruiters',fmt(x.required));set('modelRequiredRange','54,3–108,5');set('modelOpenRecruiterVacancies','12');
  set('modelRecruitingPressure',fmt(x.pressure)+'×');
  set('modelForceIndex',fmt(x.support));set('modelDeficitIndex',fmt(100-x.support));
  set('modelTrueIndex','н/д');set('modelProxyRange',fmt(low)+'–'+fmt(high)+' · база '+fmt(baseline));
  const bar=$('scenarioRange');if(bar){bar.style.left=low+'%';bar.style.width=(high-low)+'%'}
  const mark=$('scenarioBaseMarker');if(mark)mark.style.left=baseline+'%';
  set('searchSupportHud',fmt(x.support));
  document.querySelectorAll('[data-kpi="proxy_support_index"]').forEach(el=>el.textContent=fmt(x.support));
  document.querySelectorAll('[data-kpi="proxy_deficit_index"]').forEach(el=>el.textContent=fmt(100-x.support));
 }
 const capacity=$('capacitySelect');if(capacity){capacity.value='300';capacity.addEventListener('change',renderCapacity);renderCapacity()}
 const insights=$('insights');if(insights){
  insights.replaceChildren();
  const p=document.createElement('p');p.textContent='Результаты разных поисковых запросов могут пересекаться. В снимке обзора: 2 715 активных вакансий, 702 активных резюме, STOCK 0,26. Это демонстрация, а не текущая статистика.';insights.append(p);
 }
 // Only two known month-end samples are available for vacancies. Plot no invented intermediary data.
 const history=$('historyChart');
 if(history){
  history.innerHTML='<path d="M 80 40V286H852" fill="none" stroke="#36566b" stroke-width="1.5"/><path d="M80 87 L820 242" stroke="#32d5ce" stroke-width="3" fill="none"/><circle cx="80" cy="87" r="6" fill="#07202b" stroke="#32d5ce" stroke-width="2"/><circle cx="820" cy="242" r="6" fill="#07202b" stroke="#32d5ce" stroke-width="2"/><text x="85" y="66" fill="#d7e6ef" font-size="22">3 507</text><text x="715" y="224" fill="#d7e6ef" font-size="22">2 170</text><text x="80" y="320" fill="#93a9b8" font-size="16">30 июля 2026</text><text x="700" y="320" fill="#93a9b8" font-size="16">31 августа 2026</text>';
 }
 set('chartLegend','Новые вакансии за 30 дней (два подтверждённых замера)');
 for(const id of ['querySelect','metricSelect','historyRangeSelect','correlationMethodSelect','correlationLagSelect','pulseScopeSelect','pulseRangeSelect']){
  const el=$(id);if(el){el.disabled=true;el.title='Архивный просмотр: доступна только одна подтверждённая выборка';}
 }
 const stats=[['Новые вакансии за 30 дней','2 170'],['Новые резюме за 30 дней','744'],['Конкуренция STOCK','0,34'],['Конкуренция FLOW','0,34'],['HR-вакансий на 1 000 IT','5,53'],['Наблюдаемая поддержка (OBS)','92,9'],['Proxy-поддержка поиска','85,8'],['Proxy-потребность в усилении','14,2']];
 const latest=$('pulseLatest');if(latest){latest.replaceChildren();for(const [name,value] of stats){const el=document.createElement('div');el.className='demo-metric';const small=document.createElement('small');small.textContent=name;const strong=document.createElement('strong');strong.textContent=value;el.append(small,strong);latest.append(el)}}
 // Chart below recreates the *visual trend* from the supplied pulse screenshot. No original time-series data was provided.
 const series=[
 {name:'Активные вакансии',color:'#22d6cd',points:[100,129,129,128,127,85,82]},
 {name:'Активные резюме',color:'#9871ee',points:[100,100,100,100,100,103,106]},
 {name:'HR-вакансий на 1 000 IT',color:'#50c9ee',points:[100,100,100,96,97,116,92]}
 ];
 const active=new Set(series.map(x=>x.name));
 const toggles=$('pulseSeriesToggles');
 function renderPulse(){
  const s=$('pulseChart');if(!s)return;const left=80,right=1060,top=42,bottom=348,max=135,min=74;
  const x=i=>left+(right-left)*i/6,y=n=>bottom-(n-min)/(max-min)*(bottom-top);
  let markup='';
  for(const n of [75,90,105,120,135]){const yy=y(n);markup+='<path class="chart-grid-line" d="M'+left+' '+yy+'H'+right+'"/><text class="chart-axis-text" x="26" y="'+(yy+5)+'">'+n+'</text>'}
  markup+='<path d="M'+left+' '+y(100)+'H'+right+'" stroke="#60768a" stroke-dasharray="6 8" fill="none"/>';
  for(const q of series){if(!active.has(q.name))continue;
   const path=q.points.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)).join(' ');
   markup+='<path class="chart-line" stroke="'+q.color+'" d="'+path+'"/>';
   markup+=q.points.map((v,i)=>'<circle class="chart-dot" stroke="'+q.color+'" cx="'+x(i).toFixed(1)+'" cy="'+y(v).toFixed(1)+'" r="4.2"><title>'+q.name+': относительное значение '+v+' (визуализация по снимку)</title></circle>').join('');
  }
  markup+='<text class="chart-axis-text" x="80" y="394">30 июл. 2026</text><text class="chart-axis-text" x="935" y="394">31 авг. 2026</text>';
  s.innerHTML=markup;
 }
 if(toggles){toggles.replaceChildren();for(const q of series){const b=document.createElement('button');b.className='snapshot-series-toggle active';b.style.setProperty('--series-color',q.color);const i=document.createElement('i');b.append(i,document.createTextNode(q.name));b.onclick=()=>{if(active.has(q.name))active.delete(q.name);else active.add(q.name);b.classList.toggle('active',active.has(q.name));renderPulse()};toggles.append(b)}}
 renderPulse();
 set('pulseCoverageNote','Визуальная реконструкция линий по предоставленному скриншоту, без полного ряда исходных замеров. Первое значение = 100.');
 const correlation=$('pulseCorrelations');if(correlation){correlation.textContent='Для достоверного корреляционного анализа недостаточно исходных рядов в приложенных снимках.'}
 const positions=$('positionGrid');if(positions){positions.replaceChildren();const p=document.createElement('p');p.className='snapshot-note';p.textContent='В публичной демонстрации не публикуются детальные локальные поисковые запросы и база. Реальные позиции и результаты доступны в установленном приложении.';positions.append(p)}
 const posSearch=$('positionSearch');if(posSearch)posSearch.disabled=true;
})();