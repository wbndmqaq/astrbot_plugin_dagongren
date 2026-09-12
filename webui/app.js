/* 打工人·上班族物语 WebUI */
"use strict";
var curKind="wealth",allStk=[];
/* 移动端表格卡片化：为所有没有 data-th 的 td 补上列名（取自对应 thead th 文本） */
function stampTableLabels(){
  if(window.innerWidth>768)return;
  document.querySelectorAll("table").forEach(function(tb){
    var heads=Array.from(tb.querySelectorAll("thead th")).map(function(th){
      return th.textContent.replace(/\s+/g," ").trim()});
    tb.querySelectorAll("tbody tr").forEach(function(tr){
      tr.querySelectorAll("td").forEach(function(td,i){
        if(td.hasAttribute("data-th"))return;
        if(td.getAttribute("colspan")){td.classList.add("empty-cell");return}
        if(heads[i])td.setAttribute("data-th",heads[i]);
      });
    });
  });
}
function esc(s){return String(s??"").replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
/* 内联 handler（onclick / onerror 这类 on* 属性）一律不用：它们会迫使 CSP 放开
   script-src 'unsafe-inline'，而用字符串拼 handler（旧的 jsq()）又让玩家可控的
   文本——备份名、文案键名——有机会逃逸成代码。现在交互全部走 data-act + 事件
   委托（见下方 ACTIONS），参数放进 data-* 属性：在 DOM 层面它就是数据，不再
   经过 JS 字符串解析，因此也不再需要 JS 层的引号转义。 */
function fmtT(ts){var d=new Date(ts*1e3),p=function(n){return String(n).padStart(2,"0")};return p(d.getMonth()+1)+"/"+p(d.getDate())+" "+p(d.getHours())+":"+p(d.getMinutes())}
function toast(msg,cls){var b=document.getElementById("toastBox");if(!b)return;
  /* 类名必须补上 "toast-" 前缀：34 处调用点传的是 ok/warn/error，而样式表里只有
     .toast-ok/.toast-warn/.toast-error。以前直接拼裸类名（t.className="toast-msg ok"），
     于是「删除备份失败」与「已保存」的左侧色条完全相同 —— 失败提示看起来等于成功。 */
  var t=document.createElement("div");t.className="toast-msg toast-"+(cls||"ok");t.textContent=msg;
  b.appendChild(t);setTimeout(function(){t.classList.add("toast-hide");setTimeout(function(){t.remove()},380)},2600)}

/* 登录代次：每次登录成功 +1。用来区分「会话真的失效」与「登录页那一批在飞的
   401」——后者在登录成功后回调，会把刚解锁的面板用登录遮罩整个盖住
   （z-index 300 + pointer-events auto，页面直接没法点）。 */
var authGen=0;
async function api(url,opt){opt=opt||{};var gen=authGen;var r=await fetch(url,opt);
  if(!r.ok){
    var b=await r.json().catch(function(){return{}});
    // 401 仍然弹登录层，但错误文案要原样带出去：以前 401 一律抛 "unauthorized"，
    // doLogin 又把任何异常显示成「密码错误」，于是 429（被限流 300 秒）和
    // 500（会话表写失败）都长得像记错密码，运维只会继续重试、把封禁不断续上。
    // 只在同一代次内弹：跨代次的 401 是登录前发出的陈旧响应，与当前会话无关。
    if(r.status===401&&gen===authGen)showLogin();
    var err=new Error(b.error||String(r.status));err.status=r.status;throw err}
  return r.headers.get("content-type")?.includes("json")?r.json():r.text()}
function jget(u){return api(u)}
async function jpost(u,body){return api(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})})}

/* ===== 登录 ===== */
function showLogin(){document.getElementById("loginOverlay").classList.add("show")}
function hideLogin(){document.getElementById("loginOverlay").classList.remove("show")}
function showChangePwd(force){
  var ov=document.getElementById("changePwdOverlay");if(!ov)return;
  ov.classList.add("show");
  var n=document.getElementById("cpOld"),m=document.getElementById("cpNew"),r=document.getElementById("cpNew2"),e=document.getElementById("cpErr");
  if(n)n.value="";if(m)m.value="";if(r)r.value="";if(e)e.textContent="";
  var btn=document.getElementById("cpSubmit"),forced=document.getElementById("cpForced");
  _cpForced=!!force;
  if(btn){btn.disabled=false;btn.textContent=force?"立即修改并登录":"修改密码"}
  if(forced)forced.style.display=force?"":"none";
}
function hideChangePwd(){var ov=document.getElementById("changePwdOverlay");if(ov)ov.classList.remove("show")}
/* 回车提交：登录框与改密框都不在 <form> 里，也没有 keydown 监听，
   以前必须用鼠标点按钮——密码框不支持回车是最基础的可用性缺陷。 */
function _submitOnEnter(id,fn){
  var el=document.getElementById(id);if(!el)return;
  el.addEventListener("keydown",function(ev){
    if(ev.key==="Enter"){ev.preventDefault();fn()}})}
_submitOnEnter("loginPwd",function(){doLogin()});
["cpOld","cpNew","cpNew2"].forEach(function(id){
  _submitOnEnter(id,function(){submitChangePwd(document.getElementById("cpSubmit"))})});
async function submitChangePwd(btn){
  if(btn){btn.disabled=true;btn.textContent="提交中…"}
  try{
    var o=document.getElementById("cpOld").value,n=document.getElementById("cpNew").value,n2=document.getElementById("cpNew2").value;
    if(n.length<8){throw new Error("新密码至少 8 位")}
    if(n!==n2){throw new Error("两次输入的新密码不一致")}
    var r=await jpost("/api/auth/change-password",{old_password:o,new_password:n});
    toast(r.message||"密码已更新","ok");hideChangePwd();
    // 改密后 epoch 自增，旧 cookie 失效——重新登录
    try{await jpost("/api/auth/logout",{})}catch(e){}
    showLogin();
  }catch(e){var ex=document.getElementById("cpErr");if(ex)ex.textContent=e.message||String(e)}
  // 文案回原样：唯一正确的来源是 showChangePwd(force) 设的那一份，
  // finally 里无条件写死会让普通改密后的按钮标题变成「立即修改并登录」
  finally{if(btn){btn.disabled=false;btn.textContent=_cpForced?"立即修改并登录":"修改密码"}}
}
function playEnterFx(){var b=document.body;
  b.classList.remove("authed");void b.offsetWidth;b.classList.add("authed")}
/* ===== 自定义确认弹窗 ===== */
var _askResolve=null,_askKeys=null,_askReturnFocus=null,_cpForced=false;
function closeAsk(v){
  var mask=document.getElementById("askMask");
  if(!mask||!mask.classList.contains("show"))return;
  mask.classList.remove("show");
  document.removeEventListener("keydown",_askKeys);
  // 焦点还给触发弹窗的那个元素：不还的话键盘用户关掉弹窗后焦点落在 body，
  // Tab 得从页首重新走一遍
  if(_askReturnFocus&&document.contains(_askReturnFocus)){
    try{_askReturnFocus.focus()}catch(e){}
  }
  _askReturnFocus=null;
  if(_askResolve){var r=_askResolve;_askResolve=null;r(v)}
}
function askConfirm(msg,opt){
  opt=opt||{};
  var mask=document.getElementById("askMask");
  if(!mask)return Promise.resolve(window.confirm(msg));
  // 上一个弹窗还没结算时先把它按「取消」结算掉：否则它的 Promise 永久悬挂，
  // 且旧的 keydown 监听会被新的一份覆盖、永远摘不掉（每弹一次泄漏一个）。
  if(_askResolve)closeAsk(false);
  document.getElementById("askIco").textContent=opt.icon||"⚠️";
  document.getElementById("askTitle").textContent=opt.title||"确认操作";
  document.getElementById("askMsg").innerHTML=msg;
  var yes=document.getElementById("askYes"),no=document.getElementById("askNo");
  yes.textContent=opt.yes||"确定";
  no.textContent=opt.no||"取消";
  yes.className="btn "+(opt.danger===false?"":"btn-red");
  no.className="btn btn-ghost";
  mask.classList.add("show");
  _askReturnFocus=document.activeElement;
  setTimeout(function(){yes.focus()},140);
  return new Promise(function(res){
    _askResolve=res;
    _askKeys=function(e){
      if(e.key==="Escape"){e.preventDefault();closeAsk(false)}
      else if(e.key==="Enter"){e.preventDefault();closeAsk(true)}
      else if(e.key==="Tab"){
        // 焦点锁在弹窗内：不然 Tab 会走到背后被遮住的面板上
        var items=[no,yes];
        var idx=items.indexOf(document.activeElement);
        e.preventDefault();
        items[(idx+(e.shiftKey?-1:1)+items.length)%items.length].focus();
      }};
    document.addEventListener("keydown",_askKeys);
  });
}

async function doLogin(){
  var btn=document.querySelector("#loginCard .btn");
  var card=document.getElementById("loginCard");
  var pwd=document.getElementById("loginPwd");
  btn.disabled=true;btn.textContent="验证中…";
  try{
    var lr=await jpost("/api/auth/login",{password:pwd.value});
    authGen++;  /* 本次登录之后，此前在飞的 401 不再有资格弹登录层 */
    card.classList.add("out");
    setTimeout(function(){
      hideLogin();card.classList.remove("out");pwd.value="";
      playEnterFx();toast("解锁成功","ok");loadAll();
      // 会话 chip 仅在启用密码时显示；首次登录后立即拉一次会话列表
      var sc=document.getElementById("sessionsChip");
      if(sc)sc.style.display="inline-flex";
      loadSessions();
      if(lr&&lr.must_change_password)showChangePwd(true);
    },430);
  }catch(e){
    document.getElementById("loginErr").textContent=e.message||"密码错误";
    card.classList.remove("shake","err");void card.offsetWidth;
    card.classList.add("shake","err");
    setTimeout(function(){card.classList.remove("err")},650);
  }finally{btn.disabled=false;btn.textContent="解 锁"}
}
var checkGen=authGen;
jget("/api/auth/check").then(function(r){
  // 这次 check 是页面加载时发出的：若期间用户已经登录成功，它的 401 结论已经
  // 过期，不能再把登录层弹回来（同 api() 里的代次判断）
  if(checkGen===authGen&&r.required&&!r.ok)showLogin();
  var lo=document.getElementById("logoutChip");
  if(lo)lo.style.display=r.required?"inline-flex":"none";
  var sc=document.getElementById("sessionsChip");
  if(sc)sc.style.display=r.required?"inline-flex":"none";
}).catch(function(){});

async function doLogout(){
  try{await jpost("/api/auth/logout",{})}catch(e){}
  location.reload();
}

/* ===== 星尘 ===== */
(function(){var box=document.getElementById("starField");if(!box)return;
  for(var i=0;i<42;i++){var s=document.createElement("i");s.className="star";
    s.style.left=Math.random()*100+"vw";s.style.top=Math.random()*100+"vh";
    s.style.animationDelay=(Math.random()*4).toFixed(1)+"s";
    s.style.animationDuration=(2+Math.random()*3).toFixed(1)+"s";
    s.style.width=s.style.height=(Math.random()*2+1).toFixed(1)+"px";
    box.appendChild(s)}})();

/* 动态渲染后自动补列名：tbody 内容全部由 JS 渲染，
   仅靠 DOMContentLoaded/resize 无法覆盖，用 MutationObserver 兜底（debounce 60ms）。
   stamp 只写 attribute，不会触发 childList observer，无死循环。 */
var _stampTimer=null;
var _stampObserver=new MutationObserver(function(){
  if(window.innerWidth<=768){
    if(_stampTimer)clearTimeout(_stampTimer);
    _stampTimer=setTimeout(stampTableLabels,60)}});
document.addEventListener("DOMContentLoaded",function(){
  _stampObserver.observe(document.body,{childList:true,subtree:true})});

/* ===== 滚动进度条 ===== */
(function(){var bar=document.getElementById("scrollProgress");if(!bar)return;
  var ticking=false;
  function upd(){
    var h=document.documentElement;
    var max=h.scrollHeight-h.clientHeight;
    var p=max>0?Math.min(1,(h.scrollTop||document.body.scrollTop)/max):0;
    bar.style.transform="scaleX("+p+")";
    bar.classList.toggle("on",p>0.002);
    ticking=false}
  window.addEventListener("scroll",function(){if(!ticking){requestAnimationFrame(upd);ticking=true}},{passive:true});
  window.addEventListener("resize",upd);upd()})();

/* ===== 时钟 + 实时动态轮询 ===== */
/* 轮询闸门：页面被隐藏（切标签页/手机锁屏最小化）或停在登录层（未登录/已登出——
   两条登出路径最终都会 showLogin）时一律不发请求。此前两个定时器无条件跑：
   登录页上每 10 秒打一次 /api/overview，每次都 401、每次都被 api() 重新弹一遍
   登录层；后台标签页也在空转。 */
function _panelLive(){
  if(document.hidden)return false;
  var ov=document.getElementById("loginOverlay");
  return !(ov&&ov.classList.contains("show"));
}
setInterval(function(){
  if(document.hidden)return;  /* 时钟：页面不可见就别算了，回到前台下一拍自会刷新 */
  var d=new Date(),p=function(n){return String(n).padStart(2,"0")};
  var el=document.getElementById("liveClock");
  if(el)el.textContent="🕐 "+p(d.getHours())+":"+p(d.getMinutes())+":"+p(d.getSeconds())},1000);

/* ===== 实时动态：面板激活时每 10 秒自动刷新 ===== */
setInterval(function(){
  if(!_panelLive())return;
  var p=document.getElementById("p-feed");
  if(p&&p.classList.contains("on"))loadOverview()},10000);

/* ===== 清空动态（管理员） ===== */
async function clearEvents(btn){
  if(!(await askConfirm("确定清空全部实时动态记录？此操作不可恢复。",{icon:"🧹",title:"清空动态",yes:"确认清空"})))return;
  if(btn){btn.disabled=true;btn.textContent="清空中…"}
  try{await jpost("/api/admin/events/clear",{});toast("动态已清空","ok");loadOverview()}
  catch(e){toast("清空失败："+e.message,"error")}
  finally{if(btn){btn.disabled=false;btn.textContent="🧹 清空动态"}}
}

/* ===== 通用空状态 ===== */
/* icon 同样走 esc：当前调用全是常量 emoji，但它是拼进 innerHTML 的插值口，
   一旦有人把数据里的字段（或后端文案）传进来就是注入面。text 早就 esc 了。 */
function emptyState(icon,text){return '<div class="empty-state"><div style="font-size:26px;margin-bottom:6px">'+esc(icon)+'</div><div>'+esc(text)+'</div></div>'}

/* ===== 元数据 ===== */
async function loadMeta(){try{var m=await jget("/api/meta");
  document.getElementById("metaVer").textContent=m.version||"—";
  document.getElementById("metaPort").textContent="端口 "+(m.port||17817);
  var fv=document.getElementById("footerVer");
  if(fv)fv.textContent="astrbot_plugin_shangbanzu "+(m.version||"—");
  var lock=document.getElementById("lockBadge");
  if(lock)lock.style.display=m.auth_required?"inline-flex":"none";
  /* 文案库分类清单：登录前 /api/meta 只回最低限度（不带 version/port/清单），
     登录成功后 loadAll() 会再拉一次，届时才填上。 */
  if(Array.isArray(m.texts)){txCatalog=m.texts;renderCats()}
  renderRankTabs(m.rankings);
}catch(e){console.warn(e)}}

/* 榜单分类标签：单一来源是 resources/data/rankings.json（经 /api/meta 下发），
   接口没给就保留 index.html 里的静态兜底。名称是运维可编辑的 JSON 内容，
   拼进 innerHTML 前必须 esc 转义。 */
var RANK_ICONS={wealth:"💰",exp:"🔥",value:"💎",level:"👔"};
function renderRankTabs(list){
  var box=document.getElementById("rankTabs");
  if(!box||!Array.isArray(list)||!list.length)return;
  var reset=!list.some(function(r){return r.key===curKind});
  if(reset)curKind=list[0].key;
  box.innerHTML=list.map(function(r){
    var icon=RANK_ICONS[r.key]||"🏆";
    return '<button class="sub-tab'+(r.key===curKind?" on":"")+'" data-k="'
      +esc(r.key)+'">'+icon+" "+esc(r.name||r.key)+"</button>";
  }).join("");
  bindRankTabs();
  /* 榜单定义变了（运维在线改了 rankings.json）时 curKind 会被重置，
     此时表格里还是旧榜数据，标签却已高亮新榜——补拉一次 */
  if(reset&&document.getElementById("p-rank").classList.contains("on"))loadRank();
}
function bindRankTabs(){
  var box=document.getElementById("rankTabs");
  if(!box)return;
  box.querySelectorAll("button").forEach(function(btn){
    btn.addEventListener("click",function(){
      box.querySelectorAll("button").forEach(function(b){b.classList.remove("on")});
      btn.classList.add("on");curKind=btn.dataset.k;loadRank();
    });
  });
}

/* ===== 总览 ===== */
function countUp(el,target){
  var start=null,dur=900;
  function step(ts){
    if(!start)start=ts;
    var p=Math.min(1,(ts-start)/dur),e=1-Math.pow(1-p,3);
    el.textContent=Math.round(target*e).toLocaleString();
    if(p<1)requestAnimationFrame(step)}
  requestAnimationFrame(step)}
/* 上次渲染用的数据指纹：10 秒轮询只在内容真的变了时才重建 DOM。
   无条件重写 innerHTML 会让统计数字每轮从 0 重新计数、30 条动态重放动画，
   并丢掉用户在列表里的文本选中。 */
var _ovSig=null;
async function loadOverview(){try{
  var o=await jget("/api/overview"),s=o.stats||{};
  var cards=[[s.players??0,"注册玩家"],[s.groups??0,"游戏群数"],
    [s.events_today??0,"今日动态"],[(s.richest&&s.richest.nickname)||"虚位以待","全服首富"]];
  var sig=JSON.stringify([cards,o.news||""])+"|"+(o.events||[]).map(function(e){
    return e.time+"/"+e.kind+"/"+e.summary+"/"+e.gid}).join(",");
  if(sig===_ovSig)return;  /* 数据没变：不碰 DOM，动画与选中都保住 */
  _ovSig=sig;
  document.getElementById("newsText").textContent=o.news||"今日无事发生";
  document.getElementById("statCards").innerHTML=cards.map(function(c,i){
    return '<div class="stat-card" style="animation-delay:'+(i*70)+'ms">'
      +'<div class="num">'+esc(typeof c[0]==="number"?"0":String(c[0]).slice(0,14))+'</div>'
      +'<div class="lab">'+esc(c[1])+'</div></div>'}).join("");
  var nums=document.querySelectorAll("#statCards .num");
  cards.forEach(function(c,i){if(typeof c[0]==="number"&&nums[i])countUp(nums[i],c[0])});
  var hot=["裁员","住院","买房"];
  var feed=document.getElementById("feedList");feed.innerHTML="";
  if(o.events&&o.events.length){
    o.events.forEach(function(ev,i){
      var d=document.createElement("div");
      d.className="feed-item"+(hot.indexOf(ev.kind)>=0?" hot":"");
      d.style.animationDelay=(i*45)+"ms";
      d.innerHTML='<span class="feed-time">'+fmtT(ev.time)+'</span>'
        +'<span class="feed-tag">'+esc(ev.kind)+'</span>'
        +'<span>'+esc(ev.summary)+' <span class="muted">（群 '+esc(ev.gid)+'）</span></span>';
      feed.appendChild(d)});
  }else{feed.innerHTML=emptyState("💬","暂无动态，快去群里玩起来")}
}catch(e){
  document.getElementById("newsText").textContent="暂无早报";
  document.getElementById("feedList").innerHTML=emptyState("⚠️","数据加载失败，请刷新重试");
}}

async function loadGroups(){try{var g=await jget("/api/groups");
  var opts=(g.groups||[]).map(function(x){
    return '<option value="'+esc(x.gid)+'">'+esc(x.name||("群 "+x.gid))+'（'+x.count+'人）</option>'}).join("");
  ["groupSel","groupSel2","admGroupSel","playerGroupSel"].forEach(function(id){
    var el=document.getElementById(id);if(!el)return;
    var keep=el.value;
    el.innerHTML=opts||'<option value="">暂无群数据</option>';
    /* 保留已选群：整表重建会把 value 打回第一个选项，切一下面板再回来看到的
       可能已是另一个群的榜单/玩家，而界面没有任何提示。仅在原选项仍存在时恢复。 */
    if(keep&&Array.prototype.some.call(el.options,function(o){return o.value===keep}))el.value=keep;
  });
  /* 原生 select 已更新，同步重建自定义下拉（否则选项/文案停留在初始快照） */
  if(window.rebuildCustomSelects)rebuildCustomSelects();
}catch(e){
  /* 不静默：群列表拉不到时四个下拉会空着，运维只会以为"没有数据" */
  ["groupSel","groupSel2","admGroupSel","playerGroupSel"].forEach(function(id){
    var el=document.getElementById(id);if(el)el.innerHTML='<option value="">群列表加载失败</option>'});
  if(window.rebuildCustomSelects)rebuildCustomSelects();
  toast("群列表加载失败："+(e.message||e),"error");
}}

async function loadRank(){
  var gid=document.getElementById("groupSel")?.value||"";
  var b=document.getElementById("rankBody");if(!b)return;
  if(!gid){b.innerHTML='<tr><td colspan="4">'+emptyState("📊","暂无群数据，先在群里触发游戏指令")+'</td></tr>';return}
  b.innerHTML='<tr><td colspan="4"><div class="skeleton"></div></td></tr>';
  try{var r=await jget("/api/ranking?gid="+encodeURIComponent(gid)+"&kind="+curKind);
    var rows=r.rows||[];
    b.innerHTML=rows.length?rows.map(function(row,i){
      var medal=row.rank===1?"🥇":row.rank===2?"🥈":row.rank===3?"🥉":row.rank;
      return '<tr><td class="'+(row.rank===1?"rank-gold":"")+'">'+medal+'</td>'
        +'<td>'+esc(row.nickname)+'<br><span class="muted" style="font-size:11px">'+esc(row.uid)+'</span></td>'
        +'<td>'+esc(row.company)+' · '+esc(row.position)+'</td>'
        +'<td style="text-align:right;color:var(--gold);font-weight:bold">'+esc(row.score)+'</td></tr>';
    }).join(""):'<tr><td colspan="4">'+emptyState("📊","该群还没有排行数据")+'</td></tr>';
  }catch(e){b.innerHTML='<tr><td colspan="4">'+emptyState("⚠️","加载失败")+'</td></tr>'}}

/* kvItem 统一转义 value：这里的值全部来自后端返回的玩家数据（昵称、公司名、
   自建企业名都是用户可控），拼进 innerHTML 前必须转义，否则是存储型 XSS。
   已在调用处 esc 过的值不要再传进来——传原始值即可。 */
function kvItem(l,v){return '<div class="kv-item">'+esc(l)+'<b>'+esc(v)+'</b></div>'}
var _barSeq=0;
function barHtml(l,v,c){var w=Math.max(0,Math.min(100,Number(v)||0)),id="bar"+(++_barSeq);
  // 用唯一 id 而不是 [data-w='w'] 属性选择器：后者会把其它面板里同值的 bar
  // 一起改宽，且每张卡片都要全文档扫一遍。
  setTimeout(function(){var b=document.getElementById(id);if(b)b.style.width=w+"%"},80);
  return '<div class="kv-item">'+esc(l)+'<div class="bar-track"><i class="bar-fill" id="'+id+'" style="background:'+esc(c)+'"></i></div></div>'}
function profileCardHtml(p){
  return '<div class="profile-card"><div class="profile-head">'
    +(p.avatar?'<img class="avatar-lg" src="'+esc(p.avatar)+'">':"")
    +'<div><div style="font-size:18px;font-weight:bold">'+esc(p.nickname)+'</div>'
    +'<div class="muted" style="font-size:12px">'+esc(p.uid)+' · 更新于 '+fmtT(p.updated)+'</div></div></div>'
    +'<div class="info-grid">'
    +kvItem("公司",p.company+(p.tag?" · "+p.tag:""))
    +kvItem("职位",p.position)
    +kvItem("月薪",p.salary+" 元")
    +kvItem("总资产",p.total+" 元")
    +kvItem("公积金",p.fund_savings+" 元")
    +kvItem("身价",p.value+" 元")
    +kvItem("通勤方式",p.commute)
    +kvItem("调休券",p.comp_leave+" 张")
    +barHtml("❤️ 健康",p.health,"linear-gradient(90deg,#6fe08c,#2fbf71)")
    +barHtml("🧠 精神",p.mind,"linear-gradient(90deg,#7fd1ff,#5b8fd6)")
    +kvItem("卷王段位",p.tier)
    +kvItem("对线战绩",p.duel)
    +'</div></div>'}

async function doSearch(){
  var gid=document.getElementById("groupSel2")?.value||"";
  var kw=document.getElementById("kwInput")?.value?.trim()||"";
  var box=document.getElementById("searchResult");
  if(!gid||!kw){box.innerHTML=emptyState("🔍","请选择群组并输入关键字");return}
  box.innerHTML='<div class="skeleton" style="height:90px"></div>';
  try{var r=await jget("/api/search?gid="+encodeURIComponent(gid)+"&kw="+encodeURIComponent(kw));
    box.innerHTML=(r.results&&r.results.length)?r.results.map(profileCardHtml).join("")
      :emptyState("🔍","未找到该玩家");
  }catch(e){box.innerHTML=emptyState("⚠️","查询失败")}}

/* ===== 股市 ===== */
var stkEdits={};
/* 地板价由服务端下发（stock_min_price 可配，默认 0.5）。此前这里把输入框的 min 写死成固定值：
   运维调高地板价后，输入框仍提示旧的下限合法，提交才被服务端拒绝，看起来像 bug。 */
var stkMinPrice=0.5;
async function loadStocks(){try{var r=await jget("/api/stocks");allStk=r.stocks||[];
  if(r.min_price!=null)stkMinPrice=Number(r.min_price);
  renderStk(allStk)}catch(e){
  document.getElementById("stkBody").innerHTML='<tr><td colspan="4">'+emptyState("⚠️","加载失败")+'</td></tr>'}}
function renderStk(list){var b=document.getElementById("stkBody");if(!b)return;
  b.innerHTML=(list&&list.length)?list.map(function(s,i){
    return '<tr><td class="muted" style="font-size:12px">'+esc(s.code)+'</td>'
      +'<td>'+esc(s.name)+'</td>'
      +'<td><div class="stepper">'
      +'<button type="button" class="st-btn" data-step="-1" tabindex="-1" aria-label="减少">−</button>'
      +'<input type="number" step="0.01" min="'+stkMinPrice+'" class="tx-f stk-price'
      +(stkEdits[s.code]!==undefined?' stk-dirty':'')
      +'" data-code="'+esc(s.code)+'" value="'+(stkEdits[s.code]!==undefined?stkEdits[s.code]:s.price)+'">'
      +'<button type="button" class="st-btn" data-step="1" tabindex="-1" aria-label="增加">+</button></div></td>'
      +'<td style="color:'+(s.chg>=0?"var(--green)":"var(--red)")+';text-align:right">'
      +(s.chg>=0?"+":"")+s.chg+'%</td></tr>';
  }).join(""):'<tr><td colspan="4">'+emptyState("📈","暂无股票数据")+'</td></tr>';
  b.querySelectorAll(".st-btn").forEach(bindStepper)}
document.addEventListener("DOMContentLoaded",function(){
  var b=document.getElementById("stkBody");
  if(b)b.addEventListener("input",function(e){
    var t=e.target;if(!t.classList||!t.classList.contains("stk-price"))return;
    stkEdits[t.dataset.code]=t.value;
    t.classList.add("stk-dirty");
  });
});
async function saveStkEdits(btn){
  var codes=Object.keys(stkEdits).filter(function(c){return parseFloat(stkEdits[c])>0});
  if(!codes.length)return toast("没有改动的价格","warn");
  if(btn){btn.disabled=true;btn.textContent="保存中…"}
  try{
    /* 一次请求改完：逐支串行 POST 在改几十支时要等几十个往返，且首个失败
       即中断、不报告已完成的部分。后端会回成功/失败清单，这里如实提示。 */
    var r=await jpost("/api/stocks/edit-batch",{edits:codes.map(function(c){
      return {code:c,price:parseFloat(stkEdits[c])}})});
    var failed=r.failed||[];
    var applied=(r.applied||[]).length;
    if(failed.length){
      /* 两类失败分开提示：后端回 kind（price / code）。以前两类都拼成一句
         「代码不存在」，而价格越界（如低于 stock_min_price）会被谎报成代码
         不存在 —— 运维拿着一个真实存在的代码反复重试，只会怀疑面板坏了。 */
      var byPrice=[],byCode=[];
      failed.forEach(function(f){(f.kind==="price"?byPrice:byCode).push(f.code)});
      var parts=[];
      if(byPrice.length)parts.push("价格越界（允许 "+stkMinPrice+" ~ 1000000）："+byPrice.join("、"));
      if(byCode.length)parts.push("代码不存在："+byCode.join("、"));
      if(!parts.length)parts.push(failed.map(function(f){
        return f.code+"（"+f.error+"）"}).join("、"));
      toast("已更新 "+applied+" 支；"+parts.join("；")+" 未生效","warn");
    }else{
      toast("已更新 "+applied+" 支股票价格","ok");
      document.querySelectorAll(".stk-price").forEach(function(inp){
        if(stkEdits[inp.dataset.code]!==undefined){
          inp.classList.remove("stk-dirty");inp.classList.add("stk-saved")}});
    }
    setTimeout(function(){stkEdits={};loadStocks()},620);
  }catch(e){toast("保存失败："+e.message,"error")}
  finally{if(btn){btn.disabled=false;btn.textContent="💾 保存改价"}}
}
function filterStk(){var kw=(document.getElementById("stkSearch").value||"").toLowerCase();
  renderStk(allStk.filter(function(s){return !kw||s.code.indexOf(kw)>=0||s.name.toLowerCase().indexOf(kw)>=0}))}
/* 两个「全局」管理动作只差一个 URL 与提示文案，抽成一个 helper */
async function _stkAdmin(path,okMsg){
  try{var r=await api(path,{method:"POST"});
    var n=(path.indexOf("fluctuate")>=0?r.fluctuated:r.reset);
    toast(okMsg(n),"ok");loadStocks()}catch(e){toast("操作失败："+(e.message||e),"error")}}
function fluctuateAll(){return _stkAdmin("/api/stocks/fluctuate",function(n){
  return "已对 "+n+" 支股票执行波动"})}
function randomizeAll(){return _stkAdmin("/api/stocks/randomize",function(){
  return "已重置全部股价"})}

/* ===== 备份 ===== */
async function loadBackups(){try{var r=await jget("/api/backups");var items=r.backups||[];
  var b=document.getElementById("bkBody");if(!b)return;
  b.innerHTML=items.length?items.map(function(bk,i){
    return '<tr><td>'+(i+1)+'</td><td>'+esc(bk.name)+'</td>'
      +'<td>'+bk.size_kb+' KB</td><td class="muted">'+esc(bk.time)+'</td>'
      +'<td style="text-align:right;white-space:nowrap">'
      +'<button class="btn btn-ghost" style="padding:4px 12px;margin-right:6px" data-act="bkRestore" data-arg="'+esc(bk.name)+'">恢复</button> '
      +'<button class="btn btn-ghost" style="padding:4px 12px;color:var(--red)" data-act="bkDelete" data-arg="'+esc(bk.name)+'">删除</button></td></tr>';
  }).join(""):'<tr><td colspan="5">' + emptyState("🗄️","暂无备份") + '</td></tr>';
}catch(e){
  var bb=document.getElementById("bkBody");
  if(bb)bb.innerHTML='<tr><td colspan="5">'+emptyState("⚠️","备份列表加载失败")+'</td></tr>';
  toast("备份列表加载失败："+(e.message||e),"error");
}}
async function bkCreate(){var label=document.getElementById("bkLabel")?.value?.trim()||"";
  try{await jpost("/api/backups/create",{label:label});toast("备份创建成功","ok");loadBackups()}catch(e){toast("备份失败："+(e.message||e),"error")}}
async function bkRestore(name){if(!(await askConfirm("确定用「"+esc(name)+"」覆盖当前全部数据？此操作不可撤销！", {icon:"🗄️",title:"恢复备份",yes:"覆盖恢复"})))return;
  try{await jpost("/api/backups/restore",{name:name});toast("恢复成功","ok");loadBackups()}catch(e){toast("恢复失败："+(e.message||e),"error")}}
async function bkDelete(name){if(!(await askConfirm("确定删除备份「"+esc(name)+"」？删除后无法找回。", {icon:"🗑️",title:"删除备份",yes:"确认删除"})))return;
  /* 删除失败必须出声：此前是 catch(e){}，删不掉时界面毫无反馈，
     运维会以为已经删了，直到磁盘被备份撑满 */
  try{await jpost("/api/backups/delete",{name:name});toast("已删除","ok");loadBackups()}
  catch(e){toast("删除备份失败："+(e.message||e),"error")}}

/* ===== 管理（查询 + 编辑 + 删除） ===== */
var admEdit={gid:"",uid:""};
var ADM_FIELDS=[["cash","现金"],["deposit","存款"],["health","健康"],["mind","精神"],
  ["exp","经验"],["salary","月薪"],["fund_savings","公积金"],["comp_leave","调休券"],["value","身价"]];
async function admSearch(){
  var gid=document.getElementById("admGroupSel")?.value||"";
  var kw=document.getElementById("admUid")?.value?.trim()||"";
  if(!gid||!kw){document.getElementById("admResult").innerHTML=emptyState("🔍","请选择群组并输入关键字");return}
  var box=document.getElementById("admResult");
  box.innerHTML='<div class="skeleton" style="height:90px"></div>';
  try{
    /* 后端支持「精确 uid 优先、否则按昵称/群名片模糊」；404 会在 api() 里
       抛异常，因此这里不再有 r.error 分支（那是永远走不到的死代码），
       失败原因直接取服务端文案。 */
    var r=await jget("/api/admin/player?gid="+encodeURIComponent(gid)+"&uid="+encodeURIComponent(kw));
    var pf=r.profile;
    admEdit={gid:pf.gid,uid:pf.uid};
    var hint=r.matched==="uid"?"":'<div class="hint-bar">🔎 按关键字「'+esc(kw)+'」匹配到该玩家（非精确用户ID），请确认是本人后再改数值。</div>';
    box.innerHTML=hint+profileCardHtml(pf)+adminEditorHtml(pf);
  }catch(e){box.innerHTML=emptyState("⚠️",e.message||"查询失败")}}

function adminEditorHtml(p){
  return '<div class="profile-card"><div style="font-weight:bold;margin-bottom:10px;color:var(--gold)">✏️ 数值调整（保存即时生效）</div>'
    +'<div class="info-grid">'
    +ADM_FIELDS.map(function(f){
      return '<div class="kv-item">'+f[1]+'<input type="number" step="any" class="tx-f adm-in" data-k="'+esc(f[0])+'" value="'+esc(String(p[f[0]]))+'"></div>';
    }).join("")
    +'</div><div class="row" style="margin-top:12px;margin-bottom:0">'
    +'<button class="btn" data-act="admSave">💾 保存修改</button>'
    +'<button class="btn btn-red" data-act="admDelete">🗑️ 删除该玩家</button>'
    +'</div></div>';
}
async function admSave(btn){
  if(!admEdit.gid)return;
  var values={};
  document.querySelectorAll("#admResult .adm-in").forEach(function(el){
    var v=parseFloat(el.value);
    if(!isNaN(v))values[el.dataset.k]=v;
  });
  if(btn){btn.disabled=true;btn.textContent="保存中…"}
  try{
    var body=Object.assign({gid:admEdit.gid,uid:admEdit.uid},values);
    var r=await jpost("/api/admin/player/save",body);
    /* rejected 是服务端判定为非法（NaN/Infinity/类型不符）因而不予写入的列名。
       此前返回值被整个丢掉、只弹「已保存玩家数据」：运维把某个数值填成 1e400
       时前端 filter 过不了 isNaN、照发不误，服务端拒绝，界面上却和成功长得一样，
       刷新回列表才发现改的值没生效。 */
    var rej=(r&&r.rejected)||[];
    /* clamped 是「填的数值超出了该列的上下限，服务端钳到边界后【写进去了】」。
       与 rejected 是两回事，所以提示也分开：只报 rejected 时，提交 mind=-5
       会落库 0.0 而界面弹绿字「已保存玩家数据」，运维刷新才发现数值停在下限上。 */
    var cl=(r&&r.clamped)||{};
    var clKeys=Object.keys(cl);
    var label=function(k){var f=ADM_FIELDS.find(function(x){return x[0]===k});return f?f[1]:k};
    var msgs=[];
    if(rej.length)msgs.push("以下字段的值不合法，已保持原值："+rej.map(label).join("、"));
    if(clKeys.length)msgs.push("以下字段超出取值范围，已按下限/上限写入："+clKeys.map(function(k){
      return label(k)+" "+cl[k].from+" → "+cl[k].to}).join("、"));
    if(msgs.length)toast(msgs.join("；"),"warn");
    else toast("已保存玩家数据","ok");
    admSearch();
  }catch(e){toast("保存失败："+e.message,"error")}
  finally{if(btn){btn.disabled=false;btn.textContent="💾 保存修改"}}
}
async function admDelete(){
  if(!admEdit.gid)return;
  if(!(await askConfirm("确定删除玩家 <b>"+esc(admEdit.uid)+"</b> 的全部数据？此操作不可恢复！",{icon:"🗑️",title:"删除玩家",yes:"确认删除"})))return;
  try{
    await jpost("/api/admin/player/delete",{gid:admEdit.gid,uid:admEdit.uid});
    toast("已删除玩家","ok");
    document.getElementById("admResult").innerHTML=emptyState("🗑️","玩家已删除");
  }catch(e){toast("删除失败："+e.message,"error")}
}

/* ===== 事件委托：替代全部内联 onclick =====
   见文件顶部注释：内联 handler 会迫使 CSP 放开 script-src 'unsafe-inline'，
   而字符串拼 onclick 又让玩家可控的名字（备份名/文案键）有逃逸成代码的机会。
   参数改走 data-* 属性，DOM 层面就是数据，不经过 JS 字符串解析。 */
var ACTIONS={
  /* 登录 / 会话 */
  doLogin:function(){doLogin()},
  submitChangePwd:function(el){submitChangePwd(el)},
  doLogout:function(){doLogout()},
  toggleSessionsPanel:function(){toggleSessionsPanel()},
  loadSessions:function(el){loadSessions(el)},
  revokeSession:function(el){revokeSession(el)},
  revokeAllOtherSessions:function(el){revokeAllOtherSessions(el)},
  /* 总览 / 榜单 / 查询 */
  clearEvents:function(el){clearEvents(el)},
  loadRank:function(){loadRank()},
  doSearch:function(){doSearch()},
  /* 股市 */
  saveStkEdits:function(el){saveStkEdits(el)},
  fluctuateAll:function(){fluctuateAll()},
  randomizeAll:function(){randomizeAll()},
  /* 备份 */
  bkCreate:function(){bkCreate()},
  loadBackups:function(){loadBackups()},
  bkRestore:function(el){bkRestore(el.dataset.arg)},
  bkDelete:function(el){bkDelete(el.dataset.arg)},
  /* 玩家管理 */
  admSearch:function(){admSearch()},
  admSave:function(el){admSave(el)},
  admDelete:function(){admDelete()},
  /* 插件配置 */
  saveCfg:function(el){saveCfg(el)},
  loadCfg:function(){loadCfg()},
  /* 公司与文案 */
  coAdd:function(){coAdd()},
  saveCompanies:function(el){saveCompanies(el)},
  coDel:function(el){coDel(Number(el.dataset.arg))},
  switchCat:function(el){switchCat(el.dataset.arg)},
  addTxRow:function(){addTxRow()},
  saveTx:function(el){saveTx(el)},
  delSub:function(el){delSub(el.dataset.arg)},
  addSub:function(el){addSub(el.dataset.arg)},
  switchTxKey:function(el){switchTxKey(el.dataset.arg)},
  delTxRow:function(el){delTxRow(Number(el.dataset.arg))},
  /* 玩家列表分页 */
  loadPlayerList:function(el){loadPlayerList(Number(el.dataset.arg)||1)},
  prevPage:function(){prevPage()},
  nextPage:function(){nextPage()}
};
function bindDelegatedActions(){
  document.addEventListener("click",function(e){
    var t=e.target;
    var el=(t&&t.closest)?t.closest("[data-act]"):null;
    if(!el)return;
    var fn=ACTIONS[el.dataset.act];
    if(typeof fn!=="function")return;
    fn(el,e);
  });
}
/* 头像加载失败要藏起来：原先靠内联 onerror，它本身就是必须放开 script-src
   'unsafe-inline' 的原因之一。error 事件不冒泡，只能在捕获阶段接。 */
document.addEventListener("error",function(e){
  var t=e.target;
  if(t&&t.tagName==="IMG"&&t.classList&&t.classList.contains("avatar-lg"))t.style.display="none";
},true);
/* ===== Tab 切换 ===== */
document.addEventListener("DOMContentLoaded",function(){
  /* 委托绑定放在早退之前：nav 缺失也不该让整页按钮全部失效 */
  bindDelegatedActions();
  var nav=document.getElementById("mainNav");if(!nav)return;
  var ink=nav.querySelector(".nav-ink");
  function moveInk(btn){if(!ink||!btn)return;ink.style.left=btn.offsetLeft+"px";ink.style.width=btn.offsetWidth+"px"}
  nav.querySelectorAll(".nav-btn[data-p]").forEach(function(btn){
    btn.addEventListener("click",function(){
      nav.querySelectorAll(".nav-btn").forEach(function(b){b.classList.remove("on")});
      btn.classList.add("on");moveInk(btn);
      switchPanel(btn.dataset.p);
    });
  });
  function switchPanel(p){
    /* 离开「公司与文案」页前先确认未保存的公司改动：公司表格此前没有脏标记，
       切一下面板再回来，手改的工资/风险会被服务端数据整表覆盖，且毫无提示。 */
    if(coDirty&&curCat==="companies"&&p!=="company"){
      askConfirm("公司表格有未保存的修改，切换后将丢失。",{icon:"❓",title:"未保存修改",yes:"放弃并切换",danger:false})
        .then(function(ok){if(ok){coDirty=false;switchPanel(p)}});
      return;
    }
    document.querySelectorAll(".panel").forEach(function(x){x.classList.remove("on")});
    var t=document.getElementById("p-"+p);
    if(t)t.classList.add("on");
    var bn=document.getElementById("bottomNav");
    if(bn)bn.querySelectorAll(".bn-btn").forEach(function(b){b.classList.toggle("on",b.dataset.p===p)});
    var topNav=document.getElementById("mainNav");
    if(topNav){var tb=topNav.querySelector(".nav-btn[data-p=\""+p+"\"]");
      topNav.querySelectorAll(".nav-btn").forEach(function(x){x.classList.remove("on")});
      if(tb){tb.classList.add("on");var ink=topNav.querySelector(".nav-ink");if(ink)moveInk(tb);}}
    if(p==="feed")loadOverview();
    if(p==="rank"){loadGroups().then(loadRank)}  /* 等群列表填充后再渲染榜单 */
    if(p==="backup")loadBackups();
    if(p==="stocks")loadStocks()  /* 股市页没有群选择器，不必先拉群列表 */
    if(p==="company"){ensureCats();loadCompanies()}
    if(p==="cfg")loadCfg();
    if(p==="players"){loadGroups().then(function(){loadPlayerList(1)})}
    if(p==="sessions")loadSessions();
  }
  var bottomNav=document.getElementById("bottomNav");
  if(bottomNav){
    bottomNav.querySelectorAll(".bn-btn[data-p]").forEach(function(btn){
      btn.addEventListener("click",function(){
        bottomNav.querySelectorAll(".bn-btn").forEach(function(b){b.classList.remove("on")});
        btn.classList.add("on");
        switchPanel(btn.dataset.p);
        window.scrollTo({top:0,behavior:"smooth"});
      });
    });
  }
  window.addEventListener("resize",stampTableLabels);
  stampTableLabels();
  /* 绑定 index.html 里的静态兜底标签。/api/meta 返回后 renderRankTabs 会整体
     重建这些节点并再调一次 bindRankTabs，旧监听随旧节点一起回收，不会双绑。 */
  bindRankTabs();
  var kw=document.getElementById("kwInput");
  if(kw)kw.addEventListener("keydown",function(e){if(e.key==="Enter")doSearch()});
  var stk=document.getElementById("stkSearch");
  if(stk)stk.addEventListener("input",filterStk);
  /* 配置项搜索：248 项平铺时找一个键要滚半天。输入即过滤（分组标题也参与
     匹配），命中时强制展开所在分组。 */
  var cfgS=document.getElementById("cfgSearch");
  if(cfgS){
    var cfgTimer=null;
    cfgS.addEventListener("input",function(){
      clearTimeout(cfgTimer);
      cfgTimer=setTimeout(function(){cfgFilter=cfgS.value;renderCfg()},120);
    });
    cfgS.addEventListener("keydown",function(e){if(e.key==="Escape"){cfgS.value="";cfgFilter="";renderCfg()}});
  }
  var cfgAll=document.getElementById("cfgToggleAll");
  if(cfgAll){
    cfgAll.addEventListener("click",function(){
      var ds=document.querySelectorAll("#cfgForm details.cfg-group");
      if(!ds.length)return;
      /* 只要还有折叠的就全展开，否则全折叠 —— 单一按钮两态，少一个控件 */
      var anyClosed=Array.prototype.some.call(ds,function(d){return !d.open});
      Array.prototype.forEach.call(ds,function(d){d.open=anyClosed});
    });
  }
  initCustomSelects();
  playEnterFx();
  var first=nav.querySelector(".nav-btn.on");
  if(first)setTimeout(function(){moveInk(first)},100);
});

function loadAll(){loadMeta();loadOverview();loadGroups();loadStocks()}

/* ===== 插件配置 ===== */
var cfgSchema={},cfgData={},cfgHidden=[],cfgFilter="",cfgOpen={};
/* 分组中文名：按 key 的【最长前缀】匹配（year_bonus_rate 归「年终奖」而不是「年」）。
   未列出的前缀用原文兜底，不为分组而分组。 */
var CFG_GROUP_CN={
  webui:"🌐 WebUI 面板",render:"🖼️ 截图渲染",card:"📇 群名片缓存",
  push:"📢 定时推送",lottery:"🎰 彩票",review:"📝 年度考评",
  shopping:"🛍️ 购物",finance:"💰 理财",fund:"📈 基金",bank:"🏦 银行",
  stock:"📊 股市",career:"💼 职业",checkin:"💼 打卡",overtime:"🌙 加班",
  report:"📄 周报",slack:"🐟 摸鱼",job:"🔎 求职",promote:"⬆️ 晋升",
  negotiate:"🤝 谈薪",company:"🏢 公司",life2:"🏠 生活",life:"🏠 生活",
  social:"👥 社交",duel:"⚔️ 对线",rank:"🏅 排位",extra:"🎲 其他玩法",
  party:"🎉 年会",redpacket:"🧧 红包",scratch:"🎟️ 刮刮乐",
  year_bonus:"🧨 年终奖",annual:"🌴 年假",gym:"🏋️ 健身",hospital:"🏥 医院",
  teambuild:"🥳 团建",coffee:"☕ 咖啡",meal:"🍜 吃饭",stall:"🍡 摆摊",
  summit:"⛰️ 峰会",boss_task:"📌 老板任务",skill:"🎓 技能",
  create_company:"🏗️ 创业",archive:"🗄️ 归档",backup:"💾 备份",db:"🗃️ 数据库",
  event:"📰 动态",train:"🎓 培训",credit:"💳 信用",leave:"🌴 请假",
  bring_food:"🍱 带饭",commute:"🚇 通勤",nap:"😴 午休",food:"🍜 吃饭",
  housing:"🏠 住房",item:"🎒 道具",pet:"🐾 宠物",cert:"📜 考证",
  travel:"✈️ 旅行",cooldown:"⏳ 冷却"
};
var CFG_INTERNAL_GROUP="⚙️ 内部参数（性能 / 容量 / TTL，一般无需修改）";

function cfgGroupOf(k,m){
  /* invisible 的内部键单独归一组并默认折叠：一次误改 render_max_concurrency
     就能把截图并发压到 1，这类参数不该和玩法数值混在一起 */
  if(m&&m.invisible)return CFG_INTERNAL_GROUP;
  var best="";
  for(var g in CFG_GROUP_CN){
    if((k===g||k.indexOf(g+"_")===0)&&g.length>best.length)best=g;
  }
  return best?CFG_GROUP_CN[best]:"🔧 通用";
}
async function loadCfg(){
  try{
    var r=await jget("/api/admin/config");
    cfgSchema=r.schema||{};cfgData=r.config||{};cfgHidden=r.hidden_keys||[];
    renderCfg();
  }catch(e){
    var box=document.getElementById("cfgForm");
    if(box)box.innerHTML=emptyState("⚠️","配置加载失败");
  }
}
/* 单行配置的控件与文案（从 renderCfg 抽出来，供分组渲染复用） */
function cfgRow(k,i){
  var m=cfgSchema[k]||{},v=cfgData[k],tp=m.type||"string",ctrl;
  var hid=cfgHidden.indexOf(k)>=0;
  if(tp==="bool"){
    if(hid){
      /* 隐藏键里的 bool 是插件内部状态（如「下次登录必须改密」）：不下发真实值，
         也不给可点的开关。注意不能带 .cfg-in 类，否则 saveCfg 照样会把这个
         复选框的 checked 提交上去（服务端也会拒收，这里是省一次无意义的写回）。 */
      ctrl='<input type="checkbox" class="sw" disabled title="插件内部状态，由插件自行维护">';
    }else{
      ctrl='<input type="checkbox" class="sw cfg-in" data-k="'+esc(k)+'"'+(v?" checked":"")+'>';
    }
  }else if(tp==="int"||tp==="float"){
    ctrl='<div class="stepper">'
      +'<button type="button" class="st-btn" data-step="-1" tabindex="-1" aria-label="减少">−</button>'
      +'<input type="number" '+(tp==="float"?'step="any"':'step="1"')+' min="0"'
      +' class="cfg-in" data-k="'+esc(k)+'" data-tp="'+esc(tp)+'" value="'+esc(String(v==null?(m.default==null?0:m.default):v))+'">'
      +'<button type="button" class="st-btn" data-step="1" tabindex="-1" aria-label="增加">+</button></div>';
  }else if(tp==="list"){
    ctrl='<textarea class="cfg-in" data-k="'+esc(k)+'" data-tp="list" rows="2" placeholder="每行一个，逗号分隔亦可">'+esc(Array.isArray(v)?v.join("\n"):String(v==null?"":v))+'</textarea>';
  }else{
    // 隐藏键（密码 / JWT 密钥）：表单里以【掩码】输入框展示，输入新值即覆盖；
    // 后端保存时立即哈希，存储与响应里都永远不会是明文。
    // autocomplete="new-password"：值虽然总为空串，但浏览器仍可能把它当登录
    // 表单自动填充并弹「保存密码」，用 new-password 明确告诉它这是新口令字段。
    ctrl='<input type="'+(hid?"password":"text")+'" class="cfg-in" data-k="'+esc(k)+'" data-tp="string"'
      +(hid?' placeholder="留空保持不变，输入新值立即生效" autocomplete="new-password"':'')
      +' value="'+esc(hid?"":String(v==null?(m.default==null?"":m.default):v))+'">';
  }
  // 动画延迟封顶：248 项配置按 i*35ms 排下去，最后一行要 8.7 秒才从 opacity:0
  // 出现（rowIn 是 both 填充），而每次进入配置页都会重放一遍。封到 240ms 后
  // 整体仍在 0.3s 内出现。
  var delay=Math.min(i*35,240);
  return '<div class="cfg-row" style="animation-delay:'+delay+'ms"><div class="cfg-info">'
    +'<span class="cfg-name">'+
      esc(m.description && m.description !== k ? m.description : k)+
    '</span>'+
    '<span class="cfg-key">'+esc(k)+' · '+esc(tp)+'</span>'+
    (m.description && m.description !== k
      ? ''
      : ' <span style="font-size:11px;color:var(--muted,#888);margin-left:6px">(缺中文标签)</span>')
    +(m.hint?'<div class="cfg-hint">'+esc(m.hint)+'</div>':"")
    +'</div><div class="cfg-ctrl">'+ctrl+'</div></div>';
}
function renderCfg(){
  var box=document.getElementById("cfgForm");if(!box)return;
  var keys=Object.keys(cfgSchema);
  var kw=cfgFilter.trim().toLowerCase();
  /* 分组保持 schema 内的首次出现顺序（schema 是按玩法域排的），
     不用字母序——字母序会把「打卡」和「彩票」打散 */
  var groups=[],byName={};
  keys.forEach(function(k){
    var m=cfgSchema[k]||{};
    var hit=!kw||k.toLowerCase().indexOf(kw)>=0
      ||String(m.description||"").toLowerCase().indexOf(kw)>=0
      ||String(m.hint||"").toLowerCase().indexOf(kw)>=0;
    var g=cfgGroupOf(k,m);
    if(!byName[g]){byName[g]={name:g,rows:[]};groups.push(byName[g])}
    if(hit)byName[g].rows.push(k);
  });
  var shown=0;
  var html=groups.filter(function(g){return g.rows.length}).map(function(g){
    shown+=g.rows.length;
    /* 搜索时强制展开，否则命中项藏在折叠组里等于没搜到。
       默认展开，但「内部参数」组默认折叠（性能/容量/TTL，误改会直接影响运行）；
       用户手动开合过就以记忆的展开状态为准（cfgOpen 存的是「是否展开」）。 */
    var remembered=cfgOpen[g.name];
    var open=kw?true:(remembered===undefined?g.name!==CFG_INTERNAL_GROUP:remembered);
    return '<details class="cfg-group" data-g="'+esc(g.name)+'"'+(open?" open":"")+'>'
      +'<summary><span class="cfg-group-name">'+esc(g.name)+'</span>'
      +'<span class="cfg-group-n">'+g.rows.length+' 项</span></summary>'
      +'<div class="cfg-grid">'+g.rows.map(cfgRow).join("")+'</div>'
      +'</details>';
  }).join("");
  box.innerHTML=shown?html:emptyState("🔍","没有匹配的配置项");
  var meta=document.getElementById("cfgMeta");
  if(meta){
    meta.textContent=kw
      ?"匹配 "+shown+" / 共 "+keys.length+" 项"
      :("共 "+keys.length+" 项 · "+groups.filter(function(g){return g.rows.length}).length+" 组");
  }
  /* 记住展开状态：切走面板再回来不该把用户展开的组重新折叠 */
  box.querySelectorAll("details.cfg-group").forEach(function(d){
    d.addEventListener("toggle",function(){cfgOpen[d.dataset.g]=d.open});
  });
  box.querySelectorAll(".st-btn").forEach(bindStepper);
}
function bindStepper(btn){
  var inp=btn.parentNode.querySelector("input");
  if(!inp)return;
  var t=null,r=null;
  function stop(){clearTimeout(t);clearInterval(r);t=r=null;
    document.removeEventListener("mouseup",stop);
    document.removeEventListener("mouseleave",stop)}
  function bump(){
    var v=parseFloat(inp.value);if(isNaN(v))v=0;
    var fine=(inp.step&&inp.step!=="any")
      ?(parseFloat(inp.step)||1)
      :(inp.dataset.tp==="float"?0.1:1);
    var min=(inp.min!==""&&inp.min!=null)?parseFloat(inp.min):0;
    v=Math.max(min,Math.round((v+parseFloat(btn.dataset.step)*fine)*10000)/10000);
    inp.value=v;
    inp.dispatchEvent(new Event("input",{bubbles:true}));
    inp.classList.remove("st-flash");void inp.offsetWidth;inp.classList.add("st-flash");
  }
  btn.addEventListener("mousedown",function(e){
    e.preventDefault();bump();
    document.addEventListener("mouseup",stop);
    t=setTimeout(function(){r=setInterval(bump,55)},400);
  });
  btn.addEventListener("click",function(e){e.preventDefault()});
  btn.addEventListener("blur",stop);
}
async function saveCfg(btn){
  if(btn){btn.disabled=true;btn.textContent="保存中…"}
  var values={};
  document.querySelectorAll("#cfgForm .cfg-in").forEach(function(el){
    var k=el.dataset.k,tp=el.dataset.tp||"string";
    if(el.type==="checkbox"){values[k]=el.checked;return}
    if(cfgHidden.indexOf(k)>=0&&el.value==="")return;
    values[k]=el.value;
  });
  try{
    var r=await jpost("/api/admin/config/save",{values:values});
    var msg=r.persisted===false?"已应用但未能持久化":"已保存 "+r.applied+" 项配置";
    if(r.notes&&r.notes.length)msg+="；"+r.notes.join("；");
    toast(msg,r.notes&&r.notes.length?"warn":"ok");
    loadCfg();
  }catch(e){toast("保存失败："+e.message,"error")}
  finally{if(btn){btn.disabled=false;btn.textContent="💾 保存配置"}}
}

/* ===== 公司管理 ===== */
var companyData=[],coFilter="",coDirty=false;
async function loadCompanies(){
  try{
    var r=await jget("/api/admin/companies");
    companyData=(r.companies||[]).slice().sort(function(a,b){return a.id-b.id});
    coDirty=false;  /* 刚从服务端整表刷新，本地改动已被覆盖 */
    renderCompanies();
    renderCats();
  }catch(e){console.warn(e);toast("公司数据加载失败："+(e.message||e),"error")}
}
function coInput(c,f,kind){
  var v=c[f]==null?"":c[f];
  if(kind==="pct")v=(Number(v)*100).toFixed(1);
  return '<input type="text" class="co-edit" data-id="'+esc(c.id)+'" data-f="'+esc(f)+'"'
    +(kind?' data-k="'+esc(kind)+'"':'')
    +' value="'+esc(String(v))+'" placeholder="-">';
}
function renderCompanies(){
  var b=document.getElementById("companyBody");if(!b)return;
  var kw=coFilter.toLowerCase();
  var list=companyData.filter(function(c){
    return !kw||String(c.name).toLowerCase().indexOf(kw)>=0||String(c.tag||"").toLowerCase().indexOf(kw)>=0});
  var cnt=document.getElementById("coCount");
  if(cnt)cnt.textContent="共 "+companyData.length+" 家 · 显示 "+list.length+" 家";
  b.innerHTML=list.length?list.map(function(c){
    return '<tr>'
      +'<td class="co-id">'+(Number(c.id)>0?'#'+String(c.id).padStart(3,"0"):'新')+'</td>'
      +'<td style="min-width:150px">'+coInput(c,"name")+'</td>'
      +'<td style="min-width:76px">'+coInput(c,"tag")+'</td>'
      +'<td style="min-width:86px">'+coInput(c,"salary","num")+'</td>'
      +'<td style="min-width:64px">'+coInput(c,"intensity","num")+'</td>'
      +'<td style="min-width:70px">'+coInput(c,"min_exp","num")+'</td>'
      +'<td style="min-width:80px">'+coInput(c,"risk","pct")+'</td>'
      +'<td style="min-width:220px">'+coInput(c,"desc")+'</td>'
      +'<td style="text-align:right;white-space:nowrap">'
      +'<button class="btn btn-red" style="padding:4px 12px;min-height:32px" data-act="coDel" data-arg="'+esc(c.id)+'">删除</button></td>'
      +'</tr>';
  }).join(""):'<tr><td colspan="9">'+emptyState("🏢",kw?"没有匹配的公司":"暂无公司数据")+'</td></tr>';
}
document.addEventListener("DOMContentLoaded",function(){
  var body=document.getElementById("companyBody");
  if(body)body.addEventListener("input",function(e){
    var t=e.target;if(!t.classList||!t.classList.contains("co-edit"))return;
    var id=parseInt(t.dataset.id,10),f=t.dataset.f,k=t.dataset.k;
    var c=companyData.find(function(x){return x.id===id});if(!c)return;
    if(k==="num"){var n=parseFloat(t.value);c[f]=isNaN(n)?0:n}
    else if(k==="pct"){var p=parseFloat(t.value);c.risk=isNaN(p)?0:p/100}
    else c[f]=t.value;
    coDirty=true;
  });
  var search=document.getElementById("coSearch");
  if(search)search.addEventListener("input",function(){coFilter=search.value.trim();renderCompanies()});
  var tb=document.getElementById("txBody");
  if(tb){
    tb.addEventListener("input",function(e){
      var t=e.target;if(!t.classList)return;
      if(t.classList.contains("ta1")&&t.dataset.arr!==undefined&&t.dataset.arr!==""){
        /* 逐行文本域：用户在末行按回车后 value 会以 "\n" 结尾，split 出一个空串
           并原样写进 JSON。游戏内这些数组是 random.choice 的池子，抽到空串就会
           发出空消息（而且界面上看不出问题）。这里丢掉空白行。 */
        var lines=t.value.split("\n").filter(function(x){return x.trim()!==""});
        txSetP(txData,t.dataset.arr,lines);txDirty=true;
      }else if(t.classList.contains("tx-f")&&t.dataset.path){
        var v=t.type==="number"?(parseFloat(t.value)||0):t.value;
        txSetP(txData,t.dataset.path,v);txDirty=true;
      }
    });
    tb.addEventListener("change",function(e){
      var t=e.target;
      if(t.dataset&&t.dataset.chk){txSetP(txData,t.dataset.chk,t.checked);txDirty=true}
    });
  }
  var m=document.getElementById("askMask");
  if(m){
    m.addEventListener("click",function(e){if(e.target===m)closeAsk(false)});
    document.getElementById("askYes").addEventListener("click",function(){closeAsk(true)});
    document.getElementById("askNo").addEventListener("click",function(){closeAsk(false)});
  }
});
function coAdd(){
  // 草稿行用【负数】临时 ID：整张表是按 id 做行标识的（data-id / coDel），
  // 所以必须唯一；同时后端只认 id>0 为既有公司，负数一律当新行处理。
  // 早先用 max+1 会在删掉某公司后撞上它的旧 ID，把它的员工划给新公司。
  var min=companyData.reduce(function(m,c){return Math.min(m,Number(c.id)||0)},0);
  var tmp=min-1;
  companyData.push({id:tmp,name:"新公司"+(companyData.length+1),tag:"综合",salary:3500,
    intensity:5,risk:0.01,min_exp:0,desc:"",perks:[]});
  coFilter="";var s=document.getElementById("coSearch");if(s)s.value="";
  coDirty=true;
  renderCompanies();toast("已添加草稿行，点击「保存全部」生效","warn");
}
async function coDel(id){
  var c=companyData.find(function(x){return x.id===id});
  if(!c)return;
  if(!(await askConfirm("确定删除「"+esc(c.name)+"」？保存后其员工将变为失业状态。", {icon:"🏢",title:"删除公司",yes:"确认删除"})))return;
  companyData=companyData.filter(function(x){return x.id!==id});
  coDirty=true;
  renderCompanies();toast("已移除，点击「保存全部」生效","warn");
}
async function saveCompanies(btn){
  if(btn){btn.disabled=true;btn.textContent="保存中…"}
  try{
    var r=await jpost("/api/admin/companies/save",{companies:companyData});
    toast("保存成功：已按薪资重排 "+(r.count||0)+" 家公司（ID 1..N）","ok");
    loadCompanies();
  }catch(e){toast("保存失败："+e.message,"error")}
  finally{if(btn){btn.disabled=false;btn.textContent="💾 保存全部"}}
}

/* ===== 数据分类（公司 + 文案合并）===== */
/* 文案库清单由 /api/meta 的 texts[] 下发（后端扫 resources/texts/*.json 生成），
   前端不再写死库名。以前这里叫 TX_LABELS、只列了 8 个库，而目录里有 33 个
   json —— 后端 _json_get/_json_save 对任意 [a-z0-9_]+ 都放行，纯粹是前端漏了，
   另外 25 个库在面板上根本选不到。中文标签缺失时降级显示库名。 */
var txCatalog=[];
var KEY_CN={
  checkin_events:"打卡事件",slack_ok:"摸鱼心得",slack_caught:"摸鱼被抓",
  overtime_events:"加班事件",hospital_texts:"住院文案",layoff_texts:"被裁员",
  layoff_safe:"裁员幸存",leave_texts:"请假留言",promote_ok:"晋升成功",
  promote_fail:"晋升失败",resign_texts:"辞职留言",job_offer:"应聘成功",
  job_fail:"应聘失败",hop_ok:"跳槽成功",hop_fail:"跳槽失败",
  weeklyreport_ok:"周报通过",weeklyreport_fail:"周报打回",commute_late:"通勤迟到",
  headlines:"每日早报",
  takeout:"点外卖",canteen:"食堂",feast:"大餐",gym:"健身",
  stall_income:"摆摊收入",stall_fail:"摆摊失败",house_move:"搬家",
  rent_paid:"交房租",rent_failed:"房租逾期",nap_ok:"午休",nap_caught:"午休被抓",
  shopping:"购物",teambuild:"团建事件",house_owned_texts:"已购房",
  actions:"对线招式",win_lines:"获胜台词",lose_lines:"战败台词",
  jinxiu_ok:"进修成功",jinxiu_fail:"进修失败",
  negotiation_ok:"加薪成功",negotiation_fail:"加薪失败",
  yearbonus_ok:"年终奖到手",yearbonus_bad:"年终奖缩水",
  skill_learn_ok:"学技能成功",skill_learn_fail:"学技能失败",
  social_ok:"社交成功",social_fail:"社交失败",gossip_texts:"职场八卦",
  side_hustle_up:"副业升级",annual_leave:"年假文案",
  party_prizes:"年会奖品",career_advice:"职场建议",
  lend_ok:"借出成功",lend_fail:"借出被拒",ot_meal:"加班餐",
  checkup_ok:"体检正常",checkup_bad:"体检异常",
  meeting:"开会",bring_food:"带饭",reply_msg:"回消息",
  meeting_room:"抢会议室",eat_with:"同事拼饭",
  boss_task_ok:"帮领导做事·成",boss_task_fail:"帮领导做事·砸",
  summit:"行业峰会",pet_interact:"宠物互动",
  cert_ok:"考证成功",cert_fail:"考证失败",travel:"旅游"};
var FIELD_CN={text:"文案内容",cash:"现金±",health:"健康±",mind:"精神±",
  exp:"经验±",cost:"花费",amount:"金额",rank:"奖项",type:"类型",
  icon:"图标",title:"标题",usage:"指令格式",desc:"说明",commands:"指令列表"};
var PH_CN={a:"自己（发起者）的昵称",b:"对方（被 @ 的人）的昵称"};
function kcn(k){return KEY_CN[k]||k}
function fcn(f){return FIELD_CN[f]||f}
var curCat="companies";
/* 分类按钮完全依赖服务端下发的清单：还没拿到时补拉一次 /api/meta，
   拉不到就只剩「公司」一个分类（不会把用户丢在一个空工具栏里） */
function ensureCats(){
  if(txCatalog.length){renderCats();return Promise.resolve()}
  return jget("/api/meta").then(function(m){
    if(Array.isArray(m.texts))txCatalog=m.texts;
  }).catch(function(){}).then(renderCats);
}
function renderCats(){
  var box=document.getElementById("catFiles");if(!box)return;
  var html='<button class="tx-file'+(curCat==="companies"?' on':'')
    +'" data-act="switchCat" data-arg="companies">🏢 公司<i>'+companyData.length+'</i></button>';
  html+=txCatalog.map(function(t){
    var n=t&&t.name;if(!n)return "";
    return '<button class="tx-file'+(n===curCat?' on':'')
      +'" title="'+esc(n)+'" data-act="switchCat" data-arg="'+esc(n)+'">'
      +esc(t.label||n)+'</button>';
  }).join("");
  box.innerHTML=html;
}
function switchCat(cat){
  if(cat===curCat)return;
  /* 取值集合跟着下发清单放开：除了「公司」，只认服务端列出的库名。
     txName 会拼进 /api/admin/json/get 的查询串，白名单是纵深防御。 */
  if(cat!=="companies"&&!txCatalog.some(function(t){return t&&t.name===cat}))return;
  var go=function(){
    curCat=cat;
    document.getElementById("coEditor").style.display=cat==="companies"?"":"none";
    document.getElementById("txEditor").style.display=cat==="companies"?"none":"";
    renderCats();
    if(cat==="companies"){loadCompanies()}
    else{txName=cat;loadTxFile()}};
  if(curCat!=="companies"&&txDirty){
    // 必须判返回值：askConfirm resolve 的是布尔，直接 .then(go) 会让「取消」
    // 和「放弃并切换」行为一致，编辑了几十条文案后误触分类即丢数据。
    askConfirm("当前文案有未保存修改，切换后将丢失。", {icon:"❓",title:"未保存修改",yes:"放弃并切换",danger:false})
      .then(function(ok){if(ok)go()});
    return}
  if(curCat==="companies"&&coDirty){
    // 公司表格与文案编辑同一页切换，同样要拦一次（否则草稿行与改过的工资会静默丢失）
    askConfirm("公司表格有未保存的修改，切换后将丢失。", {icon:"❓",title:"未保存修改",yes:"放弃并切换",danger:false})
      .then(function(ok){if(ok){coDirty=false;go()}});
    return}
  go();
}

/* ===== 文案编辑（公司管理同款表格行内编辑）===== */
var txName="work",txData={},curKey="",txDirty=false;
function txGetP(o,p){return p.split(".").reduce(function(a,x){return a==null?a:a[x]},o)}
function txSetP(o,p,v){var ps=p.split("."),t=o;
  for(var i=0;i<ps.length-1;i++){
    var kk=ps[i];
    if(t[kk]==null)t[kk]=/^\d+$/.test(ps[i+1])?[]:{};
    t=t[kk]}
  t[ps[ps.length-1]]=v}
function txBlank(v){
  if(typeof v==="string")return"";
  if(typeof v==="number")return 0;
  if(typeof v==="boolean")return false;
  if(Array.isArray(v))return v.map(txBlank);
  if(v&&typeof v==="object"){var o={};Object.keys(v).forEach(function(k){o[k]=txBlank(v[k])});return o}
  return null}
async function loadTxFile(){
  var body=document.getElementById("txBody");
  if(body)body.innerHTML='<tr><td colspan="9"><div class="skeleton"></div></td></tr>';
  try{
    var r=await jget("/api/admin/json/get?name="+encodeURIComponent(txName));
    txData=r.data||{};
    curKey=Object.keys(txData)[0]||"";
    renderTx();
  }catch(e){
    if(body)body.innerHTML='<tr><td colspan="9">'+emptyState("⚠️","加载失败："+e.message+"（重载插件后重试）")+'</td></tr>';
  }
}
function switchTxKey(k){if(k!==curKey){curKey=k;renderTx()}}
function txUnionFields(items){
  var fs=[];items.forEach(function(it){
    Object.keys(it).forEach(function(f){if(fs.indexOf(f)<0)fs.push(f)})});
  return fs}
function txCell(path,v){
  /* path 由数据里的键名与数组下标拼成（如 "events.3.accent"），字段名来自
     被编辑的 JSON 本身；直接拼进属性值会让一个畸形字段名闭合属性并注入标签。
     所有插进 HTML 属性的位置（含 data-arg）一律走 esc。 */
  var ep=esc(path);
  if(typeof v==="number")
    return '<input type="number" step="any" class="tx-f" data-path="'+ep+'" value="'+esc(v)+'" title="'+ep+'">';
  if(typeof v==="boolean")
    return '<input type="checkbox" class="sw" data-chk="'+ep+'"'+(v?" checked":"")+'>';
  if(typeof v==="string"){
    if(v.length>80||v.indexOf("\n")>=0)
      return '<textarea class="tx-f ta1" data-path="'+ep+'" rows="2">'+esc(v)+'</textarea>';
    return '<input type="text" class="tx-f" data-path="'+ep+'" value="'+esc(v)+'">';
  }
  if(Array.isArray(v)){
    if(v.every(function(x){return typeof x==="string"}))
      return '<textarea class="tx-f ta1" data-arr="'+ep+'" rows="'+Math.max(v.length,1)+'">'+esc(v.join("\n"))+'</textarea>';
    return '<div class="sub-list">'+v.map(function(it,i){
      return '<div class="sub-row">'
        +(it&&typeof it==="object"?Object.keys(it).map(function(f){
            return '<input type="text" class="tx-f" data-path="'+esc(path+"."+i+"."+f)+'" value="'+esc(String(it[f]))+'" placeholder="'+esc(fcn(f))+'">';
          }).join(""):"")
        +'<button type="button" class="btn btn-red obj-del" data-act="delSub" data-arg="'+esc(path+"."+i)+'">✕</button></div>';
    }).join("")+'<button type="button" class="btn btn-ghost btn-sm" data-act="addSub" data-arg="'+ep+'">➕ 添加</button></div>';
  }
  if(v&&typeof v==="object")
    return '<div class="sub-list"><div class="sub-row">'+Object.keys(v).map(function(f){
      return '<input type="text" class="tx-f" data-path="'+esc(path+"."+f)+'" value="'+esc(String(v[f]))+'" placeholder="'+esc(fcn(f))+'">';
    }).join("")+'</div></div>';
  return '<input type="text" class="tx-f" data-path="'+ep+'" value="">';
}
/* 顶层值可能是数组（逐条文案 / 对象数组）、字典（titles、labels）或裸字符串
   （pick_auto、not_in_game）。早期只认数组：字典分组显示成 undefined 计数，
   点进去 items.every 直接抛异常，整个分类也存不下去。 */
function txSize(v){
  if(Array.isArray(v))return v.length;
  if(v&&typeof v==="object")return Object.keys(v).length;
  return 0;
}
function txUnit(v){
  if(Array.isArray(v))return "条数据";
  if(v&&typeof v==="object")return "个字段";
  return "段文本";
}
function renderTx(){
  var body=document.getElementById("txBody");if(!body)return;
  var keys=Object.keys(txData);
  var kp=document.getElementById("txKeys");
  if(kp)kp.innerHTML=keys.length?keys.map(function(k){
    return '<button class="tx-file'+(k===curKey?' on':'')
      +'" title="'+esc(k)+'" data-act="switchTxKey" data-arg="'+esc(k)+'">'+esc(kcn(k))
      +'<i>'+txSize(txData[k])+'</i></button>';
  }).join(""):'<span class="muted" style="font-size:12px">暂无数据</span>';
  var cnt=document.getElementById("txCount");
  if(cnt)cnt.textContent="共 "+keys.length+" 组 · 当前 "+txSize(txData[curKey])+" "+txUnit(txData[curKey]);
  var hd=document.getElementById("txHead");
  if(!keys.length||!(curKey in txData)){
    curKey=keys[0]||"";
    if(hd)hd.innerHTML="<th>#</th>";
    body.innerHTML='<tr><td>'+emptyState("📝","该分类暂无内容")+'</td></tr>';
    return;
  }
  var items=txData[curKey];
  if(!Array.isArray(items)){
    /* 非数组分组（titles / labels 字典、pick_auto 这类裸字符串）：整块交给
       txCell —— 它本身就能渲染字典（键值输入行）与长字符串（文本域），
       所以这些分组同样能在这里直接改，而不是只能看着。 */
    var ph0=document.getElementById("txPh");
    if(ph0)ph0.style.display="none";
    if(hd)hd.innerHTML='<th style="width:64px">#</th><th>内容</th>';
    body.innerHTML='<tr><td class="co-id">—</td><td>'+txCell(curKey,items)+'</td></tr>';
    return;
  }
  var allStr=items.every(function(x){return typeof x==="string"});
  var ph=document.getElementById("txPh");
  if(ph){
    var phs={};
    items.forEach(function(x){
      if(typeof x==="string")String(x).replace(/\{(\w+)\}/g,function(_,t){phs[t]=1})});
    var pk=Object.keys(phs);
    if(pk.length){
      ph.style.display="";
      ph.innerHTML='💡 本组文案支持占位符：'+pk.map(function(t){
        return '<b>{'+t+'}</b> = '+esc(PH_CN[t]||("变量 "+t))}).join('、')
        +' —— 发送时自动替换成玩家昵称，<b style="color:var(--red)">请勿删除花括号</b>';
    }else ph.style.display="none";
  }
  var delBtn='<button class="btn btn-red" style="padding:4px 12px;min-height:32px" data-act="delTxRow" data-arg="IDX">删除</button>';
  if(allStr&&items.length){
    if(hd)hd.innerHTML='<th style="width:64px">#</th><th>内容（一行一条）</th><th style="text-align:right;width:76px">操作</th>';
    body.innerHTML=items.map(function(s,i){
      return '<tr><td class="co-id">'+String(i+1).padStart(3,"0")+'</td>'
        +'<td><input type="text" class="tx-f" data-path="'+esc(curKey)+"."+i+'" value="'+esc(s)+'"></td>'
        +'<td style="text-align:right">'+delBtn.replace("IDX",i)+'</td></tr>';
    }).join("");
    return;
  }
  var fs=txUnionFields(items);
  if(hd)hd.innerHTML='<th style="width:64px">#</th>'
    +fs.map(function(f){return '<th title="'+esc(f)+'">'+esc(fcn(f))+'</th>'}).join("")
    +'<th style="text-align:right;width:76px">操作</th>';
  body.innerHTML=items.length?items.map(function(it,i){
    return '<tr><td class="co-id">'+String(i+1).padStart(3,"0")+'</td>'
      +fs.map(function(f){return '<td>'+txCell(curKey+"."+i+"."+f,it[f])+'</td>'}).join("")
      +'<td style="text-align:right">'+delBtn.replace("IDX",i)+'</td></tr>';
  }).join(""):'<tr><td colspan="9">'+emptyState("📝","空数据，点「➕ 新增数据」")+'</td></tr>';
}
function addTxRow(){
  if(!Array.isArray(txData[curKey])){
    // 非数组分组是键值/整段文本结构，直接在格子里改即可，没有「条数」的概念
    toast("该分组是键值/文本结构，请在格子里直接编辑","warn");
    return;
  }
  var arr=txData[curKey];
  arr.push(txBlank(arr.length?arr[arr.length-1]:""));
  txDirty=true;renderTx();
}
async function delTxRow(i){
  if(!(await askConfirm("确定删除第 "+(i+1)+" 条数据？", {icon:"📝",title:"删除数据",yes:"确认删除"})))return;
  txData[curKey].splice(i,1);txDirty=true;renderTx();
}
async function saveTx(btn){
  if(btn){btn.disabled=true;btn.textContent="保存中…"}
  try{
    /* 最后一道闸：递归丢掉字符串数组里的空串。文本域已经过滤过一遍，但
       「➕ 新增数据」也会往字符串数组里塞空行，用户没填就保存等于往
       random.choice 的池子里放空消息。空串在文案里没有任何合法用途。 */
    _pruneBlank(txData);
    var r=await jpost("/api/admin/json/save",{name:txName,data:txData});
    txDirty=false;
    toast("保存成功：已热更新 "+(r.keys||0)+" 组文案","ok");
    loadTxFile();
  }catch(e){toast("保存失败："+e.message,"error")}
  finally{if(btn){btn.disabled=false;btn.textContent="💾 保存全部"}}
}
function addSub(path){
  var a=txGetP(txData,path);
  if(!Array.isArray(a))a=[];
  a.push(txBlank(a.length?a[a.length-1]:""));
  txSetP(txData,path,a);txDirty=true;renderTx();
}
function delSub(path){
  var i=path.lastIndexOf("."),idx=parseInt(path.slice(i+1),10);
  var arr=txGetP(txData,path.slice(0,i));
  if(!Array.isArray(arr)||!(idx in arr))return;
  arr.splice(idx,1);txDirty=true;renderTx();
}
/* 递归清理：字符串数组去掉空白元素；对象/数组就地改，保持引用不变（txData 被
   txGetP/txSetP 按路径复用，换掉对象会让编辑中的其它路径指向旧副本）。 */
function _pruneBlank(node){
  if(Array.isArray(node)){
    for(var i=node.length-1;i>=0;i--){
      if(typeof node[i]==="string"&&node[i].trim()==="")node.splice(i,1);
      else _pruneBlank(node[i]);
    }
    return;
  }
  if(node&&typeof node==="object"){
    Object.keys(node).forEach(function(k){_pruneBlank(node[k])});
  }
}

/* ===== 玩家列表分页 ===== */
var plPage=1,plTotal=0;
async function loadPlayerList(page){
  plPage=page||1;
  var gid=document.getElementById("playerGroupSel")?.value||"";
  if(!gid)return;
  try{
    var r=await jget("/api/admin/players?gid="+encodeURIComponent(gid)+"&page="+plPage);
    plTotal=Math.ceil((r.total||0)/20);
    var info=document.getElementById("playerListInfo");
    if(info)info.textContent="共 "+(r.total||0)+" 位玩家";
    var pageInfo=document.getElementById("pageInfo");
    if(pageInfo)pageInfo.textContent="第 "+plPage+" / "+(plTotal||1)+" 页";
    var prev=document.getElementById("prevBtn");
    var next=document.getElementById("nextBtn");
    if(prev)prev.disabled=plPage<=1;
    if(next)next.disabled=plPage>=plTotal;
    var b=document.getElementById("playerListBody");if(!b)return;
    b.innerHTML=(r.players&&r.players.length)?r.players.map(function(p){
      return '<tr><td>'+esc(p.nickname)+'</td><td>'+esc(p.company)+' · '+esc(p.position)+'</td>'
        +'<td>'+p.salary+' 元</td><td>'+p.total+' 元</td>'
        +'<td>'+p.health+'</td><td>'+p.mind+'</td></tr>';
    }).join(""):'<tr><td colspan="6" class="empty-state">无数据</td></tr>';
  }catch(e){
    /* 翻页失败必须出声：此前是空 catch，点「下一页」没反应，看起来像卡住 */
    toast("玩家列表加载失败："+(e.message||e),"error")}
}
function nextPage(){if(plPage<plTotal)loadPlayerList(plPage+1)}
function prevPage(){if(plPage>1)loadPlayerList(plPage-1)}

/* ===== 我的会话（JWT + 服务端会话表） ===== */
function _relTime(ts){
  var dt=Math.floor(Date.now()/1e3)-ts;if(dt<60)return dt+" 秒前";
  if(dt<3600)return Math.floor(dt/60)+" 分钟前";
  if(dt<86400)return Math.floor(dt/3600)+" 小时前";
  return Math.floor(dt/86400)+" 天前";
}
function _shortUA(ua){
  ua=String(ua||"未知设备");
  // 简易归类：浏览器 / 系统
  var browser=/Edg\//.test(ua)?"Edge":/Chrome\//.test(ua)?"Chrome":/Firefox\//.test(ua)?"Firefox":/Safari\//.test(ua)?"Safari":/bot|crawl|spider/i.test(ua)?"Bot":"浏览器";
  var os=/Windows NT/.test(ua)?"Windows":/Mac OS X|macOS/.test(ua)?"macOS":/Android/.test(ua)?"Android":/iPhone|iPad|iOS/.test(ua)?"iOS":/Linux/.test(ua)?"Linux":"未知系统";
  return browser+" · "+os;
}
async function loadSessions(btn){
  if(btn){btn.disabled=true;btn.textContent="加载中…"}
  var box=document.getElementById("sessionsList");
  if(box)box.innerHTML='<div class="skeleton" style="height:120px"></div>';
  try{
    var r=await jget("/api/auth/sessions");
    var list=r.sessions||[];
    if(!list.length){if(box)box.innerHTML=emptyState("🪪","当前没有活跃会话");return}
    var rows=list.map(function(s){
      var cur=s.current?'<span class="chip" style="background:var(--gold);color:#000;margin-left:8px">当前设备</span>':"";
      return '<div class="profile-card" style="margin-bottom:10px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">'
        +'<div style="flex:1;min-width:240px">'
        +'<div style="font-weight:bold">'+esc(_shortUA(s.user_agent))+' '+cur+'</div>'
        +'<div class="muted" style="font-size:12px;margin-top:4px">IP '+esc(s.ip||"?")+' · 创建 '+fmtT(s.created_at)+' · 最后活跃 '+esc(_relTime(s.last_seen_at))+'</div>'
        +'</div>'
        +'<div><button class="btn btn-red" data-jti="'+esc(s.jti)+'" data-current="'+esc(s.current?"1":"0")+'" data-act="revokeSession">⛔ 撤销</button></div>'
        +'</div>';
    }).join("");
    if(box)box.innerHTML=rows;
  }catch(e){
    if(box)box.innerHTML=emptyState("⚠️","加载失败："+(e.message||String(e)));
  }finally{
    if(btn){btn.disabled=false;btn.textContent="↻ 刷新"}
  }
}
async function revokeSession(btn){
  var jti=btn.dataset.jti,isCurrent=btn.dataset.current==="1";
  var msg=isCurrent
    ?"确定撤销当前设备的会话？撤销后需重新登录。"
    :"确定下线该设备？该设备的浏览器将立即失去访问权限。";
  if(!(await askConfirm(msg,{icon:"⛔",title:isCurrent?"撤销当前会话":"撤销其他会话",yes:"撤销",danger:true})))return;
  try{
    await jpost("/api/auth/sessions/revoke",{jti:jti});
    toast(isCurrent?"已撤销，请重新登录":"已撤销该会话","ok");
    if(isCurrent){
      // 服务端已 del_cookie；前端重新拉登录页
      setTimeout(function(){location.reload()},420);
    }else{
      loadSessions();
    }
  }catch(e){toast("撤销失败："+e.message,"error")}
}
async function revokeAllOtherSessions(btn){
  if(!(await askConfirm("将撤销当前设备之外的全部会话（其他浏览器/设备立即下线），确定？",{icon:"⛔",title:"撤销其他会话",yes:"全部撤销",danger:true})))return;
  if(btn){btn.disabled=true;btn.textContent="撤销中…"}
  try{
    /* 一次 DELETE：此前是「先查列表再逐个撤销」，N 次往返 + 中途失败只留一半 */
    var r=await jpost("/api/auth/sessions/revoke-others",{});
    toast("已撤销 "+(r.revoked||0)+" 个其他会话","ok");
    loadSessions();
  }catch(e){toast("批量撤销失败："+e.message,"error")}
  finally{if(btn){btn.disabled=false;btn.textContent="⛔ 撤销其他全部"}}
}
function toggleSessionsPanel(){
  // 切到 sessions 面板（chip 走与 nav-btn 一致的路径）
  var nav=document.getElementById("mainNav");
  if(nav){
    var btn=nav.querySelector('.nav-btn[data-p="sessions"]');
    if(btn)btn.click();
  }
}

loadAll();

/* ===== 自定义下拉组件 ===== */
/* 选项面板重建 + 事件绑定，initCustomSelects 与 rebuildCustomSelects 共用一份：
   此前两处各抄了一遍（含各自一份 sel-opt 点击处理），改一处漏一处。 */
function _paintSel(wrap, sel){
  var trigger=wrap.querySelector(".sel-trigger"),list=wrap.querySelector(".sel-list");
  if(!trigger||!list)return;
  var opts=Array.from(sel.options).map(function(o){
    return{value:o.value,text:o.textContent,selected:o.selected}});
  var label=opts.find(function(o){return o.selected});
  trigger.querySelector(".sel-label").textContent=label?label.text:(opts[0]?opts[0].text:"请选择");
  list.innerHTML=opts.map(function(o,i){
    return '<div class="sel-opt'+(o.selected?' sel':'')+'" role="option" tabindex="-1"'
      +' aria-selected="'+(o.selected?"true":"false")+'" data-i="'+esc(i)+'" data-v="'+esc(o.value)+'">'
      +esc(o.text)+'</div>';
  }).join("");
  function choose(opt){
    sel.value=opt.dataset.v;
    sel.dispatchEvent(new Event("change",{bubbles:true}));
    list.querySelectorAll(".sel-opt").forEach(function(o){
      o.classList.remove("sel");o.setAttribute("aria-selected","false")});
    opt.classList.add("sel");opt.setAttribute("aria-selected","true");
    trigger.querySelector(".sel-label").textContent=opt.textContent;
    closeAllDropdowns();
    trigger.focus();
  }
  list.querySelectorAll(".sel-opt").forEach(function(opt){
    opt.addEventListener("click",function(e){e.stopPropagation();choose(opt)});
  });
  return {trigger:trigger,list:list,choose:choose};
}
function _openSel(wrap, trigger, list, focusIdx){
  closeAllDropdowns(wrap);
  wrap.classList.add("open");
  trigger.setAttribute("aria-expanded","true");
  var items=Array.from(list.querySelectorAll(".sel-opt"));
  var cur=items.findIndex(function(o){return o.classList.contains("sel")});
  var idx=focusIdx===undefined?(cur<0?0:cur):focusIdx;
  if(items[idx]&&idx>=0)items[idx].focus();
  /* 展开后把高度限制在视口内，避免长列表（100 支股票）在手机上溢出 */
  list.style.maxHeight=Math.max(120,Math.min(320,window.innerHeight-120))+"px";
  list.style.overflowY="auto";
}
function _moveSel(wrap, list, delta){
  var items=Array.from(list.querySelectorAll(".sel-opt"));
  if(!items.length)return;
  var cur=items.indexOf(document.activeElement);
  var next=items[(cur<0?(delta>0?0:items.length-1):cur+delta+items.length)%items.length];
  if(next)next.focus();
}
function initCustomSelects(){
  document.querySelectorAll("select").forEach(function(sel){
    if(sel.closest(".sel-wrap"))return;
    var wrap=document.createElement("div");
    wrap.className="sel-wrap";
    wrap.innerHTML='<div class="sel-trigger" tabindex="0" role="combobox"'
      +' aria-haspopup="listbox" aria-expanded="false">'
      +'<span class="sel-label"></span><span class="sel-arrow"></span></div>'
      +'<div class="sel-list" role="listbox"></div>';
    sel.style.display="none";
    sel.parentNode.insertBefore(wrap,sel);
    wrap.appendChild(sel);
    var parts=_paintSel(wrap,sel);
    if(!parts)return;
    var trigger=parts.trigger,list=parts.list,choose=parts.choose;
    trigger.addEventListener("click",function(e){
      e.stopPropagation();
      if(wrap.classList.contains("open")){
        wrap.classList.remove("open");
        trigger.setAttribute("aria-expanded","false");
      }else{
        _openSel(wrap,trigger,list);
      }
    });
    /* 键盘可用：原生 select 被换成 div 后，此前只绑了 click —— Enter/Space/
       方向键全部无效，群组选择等控件完全没法用键盘操作。 */
    trigger.addEventListener("keydown",function(e){
      if(e.key==="Enter"||e.key===" "||e.key==="ArrowDown"){
        e.preventDefault();_openSel(wrap,trigger,list,e.key==="ArrowDown"?0:undefined);
      }else if(e.key==="ArrowUp"){e.preventDefault();_openSel(wrap,trigger,list)}
      else if(e.key==="Escape"){e.preventDefault();closeAllDropdowns();trigger.focus()}
    });
    list.addEventListener("keydown",function(e){
      if(e.key==="ArrowDown"){e.preventDefault();_moveSel(wrap,list,1)}
      else if(e.key==="ArrowUp"){e.preventDefault();_moveSel(wrap,list,-1)}
      else if(e.key==="Enter"||e.key===" "){
        e.preventDefault();
        if(document.activeElement&&document.activeElement.classList.contains("sel-opt"))
          choose(document.activeElement);
      }else if(e.key==="Escape"){e.preventDefault();closeAllDropdowns();trigger.focus()}
      else if(e.key==="Tab"){closeAllDropdowns()}
    });
  });
  document.addEventListener("click",function(){closeAllDropdowns()});
}
function closeAllDropdowns(except){
  document.querySelectorAll(".sel-wrap.open").forEach(function(w){
    if(w!==except){
      w.classList.remove("open");
      var t=w.querySelector(".sel-trigger");
      if(t)t.setAttribute("aria-expanded","false");
    }
  });
}

/* 群数据异步加载后重建自定义下拉的选项与文案（initCustomSelects 只做首帧快照） */
function rebuildCustomSelects(){
  document.querySelectorAll(".sel-wrap").forEach(function(wrap){
    var sel=wrap.querySelector("select");if(!sel)return;
    _paintSel(wrap,sel);
  });
}
