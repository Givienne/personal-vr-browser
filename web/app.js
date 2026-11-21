'use strict';
const $=id=>document.getElementById(id);
const state={game:null,query:'',sort:'name',order:'asc',page:1,total:0,all:0,seq:0,items:[]};
function cell(text,cls){const td=document.createElement('td');if(cls)td.className=cls;td.textContent=text??'';return td;}
function sizeMB(s){const n=Number(s);return Number.isFinite(n)&&n>0?(n>=1024?(n/1024).toFixed(2)+' GiB':n.toFixed(n<10?1:0)+' MiB'):'—';}
function bytes(n){if(!Number.isFinite(n)||n<0)return '—';return n>=1024**3?(n/1024**3).toFixed(2)+' GiB':n>=1024**2?(n/1024**2).toFixed(1)+' MiB':n>=1024?(n/1024).toFixed(0)+' KiB':n+' B';}
function notify(s){const d=$('notice');d.textContent=s;d.classList.add('show');clearTimeout(notify.t);notify.t=setTimeout(()=>d.classList.remove('show'),3000);}
function showMsg(s){const r=document.createElement('tr');const c=cell(s,'message');c.colSpan=5;r.append(c);$('files-body').replaceChildren(r);}
// Keep the preview outside the scrollable table so it cannot be clipped.
const hoverCover={node:null,image:null};
function ensureCoverPopout(){
 if(hoverCover.node)return;
 const node=document.createElement('div');node.className='cover-popout';node.hidden=true;
 node.setAttribute('role','img');node.setAttribute('aria-label','Enlarged game cover preview');
 const image=document.createElement('img');image.alt='';node.append(image);
 document.body.append(node);hoverCover.node=node;hoverCover.image=image;
 image.onerror=hideCoverPopout;
}
function hideCoverPopout(){if(hoverCover.node)hoverCover.node.hidden=true;}
function showCoverPopout(anchor,url){
 ensureCoverPopout();
 hoverCover.image.src=url;hoverCover.node.hidden=false;
 const a=anchor.getBoundingClientRect();
 const margin=12,gap=12,w=224,h=224;
 const right=a.right+gap,left=a.left-w-gap;
 const x=right+w+margin<=innerWidth?right:left>=margin?left:Math.max(margin,innerWidth-w-margin);
 const y=Math.min(Math.max(margin,a.top-12),Math.max(margin,innerHeight-h-margin));
 hoverCover.node.style.left=x+'px';hoverCover.node.style.top=y+'px';
}
window.addEventListener('scroll',hideCoverPopout,true);
window.addEventListener('resize',hideCoverPopout);
function makeRow({name,type,size,version,date,onClick,href,cover}){
 const r=document.createElement('tr');r.className='entry';
 const kind=cell(type,'kind');
 if(type==='📁'&&typeof cover==='string'&&cover.startsWith('/covers/')){
   const btn=document.createElement('button');btn.type='button';btn.className='cover-open';
   btn.setAttribute('aria-label','Open '+name);btn.title='Open '+name;
   btn.onclick=()=>{hideCoverPopout();onClick?.();};
   const img=document.createElement('img');img.className='cover-thumb';img.src=cover;
   img.alt='';img.loading='lazy';
   img.onerror=()=>{hideCoverPopout();kind.replaceChildren(document.createTextNode('📁'));};
   btn.append(img);kind.replaceChildren(btn);
   btn.addEventListener('pointerenter',e=>{if(e.pointerType!=='touch')showCoverPopout(btn,cover);});
   btn.addEventListener('pointerleave',hideCoverPopout);
   btn.addEventListener('focus',()=>showCoverPopout(btn,cover));
   btn.addEventListener('blur',hideCoverPopout);
 }
 r.append(kind);
 const n=cell('','name'), a=document.createElement(href?'a':'button');a.textContent=name;a.title=name;
 if(href){a.href=href;a.download='';}else{a.type='button';a.onclick=onClick;}
 n.append(a);r.append(n);r.append(cell(size,'size'),cell(version,'version'),cell(date,'date'));return r;
}
function goHome(){hideCoverPopout();state.game=null;state.page=1;history.pushState({},'',location.pathname);render();}
async function render(){
 hideCoverPopout();
 const token=++state.seq;const root=!state.game;
 $('crumb-tail').textContent=root?'':' / '+(state.game.name||state.game.id);
 $('search-wrap').hidden=!root||$('search-wrap').dataset.show!=='true';
 $('sort-select').disabled=!root;
 $('game-extra').hidden=true;
 if(root){
   showMsg('loading...');
   const params=new URLSearchParams({q:state.query,sort:state.sort,order:state.order,page:String(state.page),perPage:'50'});
   try{
     const r=await fetch('/api/games?'+params,{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);
     const d=await r.json();if(token!==state.seq)return;
     state.items=d.items;state.total=d.total;state.all=d.all;
     const frag=document.createDocumentFragment();
     for(const g of d.items)frag.append(makeRow({name:g.name,type:'📁',size:sizeMB(g.sizeMB),version:g.version||'—',date:g.lastUpdated||'—',cover:g.coverUrl,onClick:()=>openGame(g.id,g.name)}));
     if(!d.items.length){showMsg(d.all?'no games match your search':'no games found — VRSRC source not synchronized');}
     else $('files-body').replaceChildren(frag);
     $('count-text').textContent=`${d.total} game${d.total===1?'':'s'} / ${d.all} total`;
     const pages=Math.max(1,Math.ceil(d.total/50));$('page-text').textContent=pages>1?`${state.page} / ${pages}`:'';
     $('prev').hidden=state.page<=1;$('next').hidden=state.page>=pages;
   }catch(e){if(token===state.seq){showMsg('could not load game list');notify(e.message);}}
 }else{
   $('prev').hidden=true;$('next').hidden=true;$('page-text').textContent='';showMsg('loading...');
   try{
     const r=await fetch('/api/games/'+encodeURIComponent(state.game.id),{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);
     const g=await r.json();if(token!==state.seq)return;
     const frag=document.createDocumentFragment();
     for(const f of g.files||[])frag.append(makeRow({name:f.name,type:'📦',size:bytes(f.size),version:'',date:'',href:f.url}));
     $('files-body').replaceChildren(frag);
     if(!g.files?.length)showMsg('no download files available for this game');
     const meta=[g.packageName,g.version&&'v'+g.version,g.lastUpdated].filter(Boolean).join('  ·  ');
     $('game-title').textContent=g.name||state.game.name||'Game';
     $('game-meta').textContent=meta;
     $('game-desc').textContent=g.description||'';
     const coverImage=$('game-cover-image'),coverMissing=$('game-cover-placeholder');
     if(typeof g.coverUrl==='string'&&g.coverUrl.startsWith('/covers/')){
       coverImage.alt='Cover art for '+(g.name||state.game.name||'game');
       coverImage.onerror=()=>{coverImage.hidden=true;coverMissing.hidden=false;};
       coverImage.src=g.coverUrl;coverImage.hidden=false;coverMissing.hidden=true;
     }else{coverImage.hidden=true;coverImage.removeAttribute('src');coverMissing.hidden=false;}
     $('game-extra').hidden=false;
     $('count-text').textContent=`${(g.files||[]).length} file${(g.files||[]).length===1?'':'s'}`;
   }catch(e){if(token===state.seq){showMsg('could not open game');notify(e.message);}}
 }
}
function openGame(id,name){state.game={id,name};history.pushState({id,name},'',`?game=${encodeURIComponent(id)}`);render();}
function sourceStatus(){fetch('/api/status',{cache:'no-store'}).then(r=>r.json()).then(s=>{
 const status=s.source||{};
 $('source-state').textContent=status.state==='ready'?'VRSRC catalog ready':status.state==='syncing'?'syncing VRSRC…':status.state==='error'?'source sync error':status.state==='authorization_required'?'catalog offline · access pending':'VRSRC not configured';
}).catch(()=>$('source-state').textContent='server offline');}
function init(){
 $('browse-btn').onclick=goHome;$('tree-root').onclick=goHome;$('crumb-root').onclick=goHome;
 $('search-btn').onclick=()=>{goHome();const w=$('search-wrap');w.dataset.show=w.dataset.show==='true'?'false':'true';w.hidden=w.dataset.show!=='true';if(!w.hidden)$('search-input').focus();};
 $('reload-btn').onclick=()=>{sourceStatus();render();};
 $('search-input').oninput=e=>{clearTimeout(init.t);state.query=e.target.value.trim();state.page=1;init.t=setTimeout(render,160);};
 $('sort-select').onchange=e=>{[state.sort,state.order]=e.target.value.split(':');state.page=1;render();};
 $('prev').onclick=()=>{state.page=Math.max(1,state.page-1);render();};$('next').onclick=()=>{state.page++;render();};
 window.onpopstate=e=>{state.game=e.state?.id?{id:e.state.id,name:e.state.name}:null;render();};
 const id=new URLSearchParams(location.search).get('game');if(id&&/^[\w.-]{1,120}$/.test(id))state.game={id,name:id};
 sourceStatus();render();
}
init();
