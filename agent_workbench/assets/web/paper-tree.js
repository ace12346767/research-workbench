import * as THREE from 'three';
import {OrbitControls} from './vendor/three/OrbitControls.js';

const palette=['#238c83','#457fc2','#b67833','#b55670','#7370b5','#5c8a49'];
const hash=value=>[...value].reduce((n,c)=>(Math.imul(n,31)+c.charCodeAt(0))>>>0,7);
const point=(topic,year,slot)=>{
  const angle=slot!=null?2.5+slot*2.399963229728653:(hash(topic)%3600)/3600*Math.PI*2;
  const radius=3+Math.max(0,year-2000)*.14;
  return new THREE.Vector3(Math.cos(angle)*radius,(year-2020)*2.3,Math.sin(angle)*radius*.65);
};

export function createPaperTree(host,{onSelect,onError}){
  const scene=new THREE.Scene();scene.background=new THREE.Color('#fafcfc');
  const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.outputColorSpace=THREE.SRGBColorSpace;
  host.append(renderer.domElement);renderer.domElement.setAttribute('aria-label','论文时间主题树');
  renderer.domElement.setAttribute('role','img');
  const labels=document.createElement('div');labels.className='tree-labels';host.append(labels);
  const camera=new THREE.PerspectiveCamera(42,1,.1,3000);
  const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=false;controls.enablePan=true;
  controls.minDistance=3;controls.maxDistance=400;controls.maxPolarAngle=Math.PI*.93;
  scene.add(new THREE.HemisphereLight(0xffffff,0x8d9dab,2.2));
  const light=new THREE.DirectionalLight(0xffffff,2);light.position.set(6,10,12);scene.add(light);
  let group=new THREE.Group(),nodes=[],annotations=[],selected=null,frame=0,disposed=false;
  let center=new THREE.Vector3(),extent=12;
  scene.add(group);
  const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();
  function curve(points,color,radius=.025){
    const geometry=new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points),32,radius,5,false);
    group.add(new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color,roughness:.8})));
  }
  function annotation(position,text,kind){const el=document.createElement('span');el.className=`tree-annotation ${kind}`;el.textContent=text;labels.append(el);annotations.push({position,el});}
  function draw(){
    frame=0;if(disposed || !host.clientWidth || !host.clientHeight)return;
    for(const item of nodes)item.mesh.scale.setScalar(item.id===selected?1.45:1);
    renderer.render(scene,camera);
    const boxes=[],w=host.clientWidth,h=host.clientHeight;
    const locate=(position,el)=>{
      const p=position.clone().project(camera);const x=(p.x+1)*w/2,y=(1-p.y)*h/2;
      el.hidden=p.z<0 || p.z>1 || x<0 || y<0 || x>w || y>h;
      return {x,y};
    };
    for(const item of annotations){const {x,y}=locate(item.position,item.el);item.el.style.transform=`translate(${x}px,${y}px)`;
      if(!item.el.hidden){const box={left:x,top:y,right:x+item.el.offsetWidth,bottom:y+item.el.offsetHeight};
        if(box.right>w||box.bottom>h||boxes.some(v=>box.left<v.right&&box.right>v.left&&box.top<v.bottom&&box.bottom>v.top))item.el.hidden=true;
        else boxes.push(box);}}
    const ordered=[...nodes].sort((a,b)=>(b.id===selected)-(a.id===selected) || a.order-b.order);
    for(const item of ordered){
      item.leader.hidden=true;
      const {x,y}=locate(item.position,item.el);if(item.el.hidden)continue;
      const width=Math.min(w<600?156:210,w-24),height=59,left=Math.max(8,Math.min(w-width-8,x+12));
      let top=Math.max(6,Math.min(h-height-6,y-24));
      const candidates=[top,top+64,top-64];let box=null;
      for(const candidate of candidates){for(const side of [left,Math.max(8,Math.min(w-width-8,x-width-12))]){const b={left:side,top:candidate,right:side+width,bottom:candidate+height};
        if(b.top<6||b.bottom>h-6)continue;
        if(!boxes.some(v=>b.left<v.right+8&&b.right>v.left-8&&b.top<v.bottom+6&&b.bottom>v.top-6)){box=b;break;}}if(box)break;}
      if(!box){item.el.hidden=true;continue;}
      boxes.push(box);item.el.style.width=width+'px';item.el.style.transform=`translate(${box.left}px,${box.top}px)`;
      const endX=x<box.left?box.left:x>box.right?box.right:x,endY=box.top+height/2,dx=endX-x,dy=endY-y;
      item.leader.hidden=false;item.leader.style.width=Math.hypot(dx,dy)+'px';item.leader.style.transform=`translate(${x}px,${y}px) rotate(${Math.atan2(dy,dx)}rad)`;
      item.el.classList.toggle('selected',item.id===selected);
    }
    renderer.domElement.dataset.rendered='true';
  }
  function schedule(){if(!frame && !disposed)frame=requestAnimationFrame(draw);}
  function resize(){const w=host.clientWidth,h=host.clientHeight;if(!w||!h)return;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();schedule();}
  const observer=new ResizeObserver(resize);observer.observe(host);controls.addEventListener('change',schedule);
  function reset(){
    const distance=Math.max(12,extent/Math.min(1,camera.aspect)*1.65);
    controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(distance*.24,distance*.12,distance));
    camera.lookAt(center);controls.update();schedule();
  }
  function setPapers(papers){
    group.traverse(item=>{item.geometry?.dispose();if(item.material){for(const m of [item.material].flat())m.dispose();}});
    scene.remove(group);group=new THREE.Group();scene.add(group);labels.replaceChildren();nodes=[];annotations=[];
    const years=papers.map(p=>p.metadata?.year).filter(Boolean),min=years.length?Math.min(...years):2020,max=years.length?Math.max(...years):2020;
    const unknown=min-2;
    curve([new THREE.Vector3(0,(min-2020)*2.3-2,0),new THREE.Vector3(0,(max-2020)*2.3+2,0)],'#788f98',.05);
    const span=max-min,step=span>24?Math.ceil(span/12):1;
    for(let year=min;years.length && year<=max;year+=step){
      const y=(year-2020)*2.3;
      curve([new THREE.Vector3(-.25,y,0),new THREE.Vector3(.25,y,0)],'#99afb5',.03);
      annotation(new THREE.Vector3(-.7,y,0),String(year),'year');
    }
    const topics=[...new Set(papers.filter(p=>p.metadata?.year).map(p=>p.metadata?.topic||'未分类'))].sort();
    for(const topic of topics){
      const members=papers.filter(p=>(p.metadata?.topic||'未分类')===topic&&p.metadata?.year);
      const sorted=[...new Set(members.map(p=>p.metadata.year))].sort((a,b)=>a-b);
      const color=palette[hash(topic)%palette.length];
      const slot=members.find(p=>p.topic_slot!=null)?.topic_slot;
      const start=point(topic,sorted[0],slot);
      curve([new THREE.Vector3(0,start.y-1,0),start,...sorted.slice(1).map(y=>point(topic,y,slot))],color,.035);
      annotation(point(topic,sorted.at(-1),slot).add(new THREE.Vector3(0,1,0)),topic,'topic');
    }
    const ordered=[...papers].sort((a,b)=>(b.metadata?.year||0)-(a.metadata?.year||0)||a.paper_id.localeCompare(b.paper_id));
    for(const [index,paper] of ordered.entries()){
      const meta=paper.metadata||{},topic=meta.topic||'未分类',year=meta.year||unknown;
      const stem=point(topic,year,paper.topic_slot),seed=hash(paper.paper_id);
      const offset=new THREE.Vector3((seed%101-50)/55,(Math.floor(seed/101)%101-50)/95,(Math.floor(seed/10201)%101-50)/65);
      const position=stem.clone().add(offset);const color=meta.year?palette[hash(topic)%palette.length]:'#a3aab4';
      curve([stem,position],color,.018);
      const mesh=new THREE.Mesh(new THREE.SphereGeometry(.14,14,10),new THREE.MeshStandardMaterial({color,roughness:.42,metalness:.2}));
      mesh.position.copy(position);mesh.userData.paperId=paper.paper_id;group.add(mesh);
      const el=document.createElement('button');el.type='button';el.className='tree-paper';el.dataset.paperId=paper.paper_id;
      el.style.borderLeftColor=color;
      el.dataset.position=JSON.stringify(position.toArray());
      const title=document.createElement('strong');title.textContent=meta.title||paper.filename;
      const sub=document.createElement('small');sub.textContent=`${meta.year||'年份待确认'}${meta.year_type==='preprint'?' 预印本':''} · ${meta.venue||'出处待确认'}`;
      el.append(title,sub);el.title=`${title.textContent}\n${sub.textContent}\n${topic}`;
      const leader=document.createElement('span');leader.className='tree-leader';leader.style.background=color;
      el.onclick=()=>onSelect(paper.paper_id);labels.append(leader,el);nodes.push({id:paper.paper_id,position,el,leader,mesh,order:index});
    }
    if(papers.some(p=>!p.metadata?.year))annotation(new THREE.Vector3(0,(unknown-2020)*2.3,0),'年份待确认','year');
    const bounds=new THREE.Box3().setFromObject(group);bounds.getCenter(center);const size=bounds.getSize(new THREE.Vector3());extent=Math.max(size.x,size.y,size.z,8);
    resize();reset();
  }
  let down=null;
  renderer.domElement.addEventListener('pointerdown',e=>{down={x:e.clientX,y:e.clientY};});
  renderer.domElement.addEventListener('pointerup',e=>{
    if(!down||Math.hypot(down.x-e.clientX,down.y-e.clientY)>5)return;
    const r=renderer.domElement.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);
    raycaster.setFromCamera(pointer,camera);const hit=raycaster.intersectObjects(nodes.map(n=>n.mesh))[0];if(hit)onSelect(hit.object.userData.paperId);
  });
  renderer.domElement.addEventListener('webglcontextlost',e=>{e.preventDefault();onError(new Error('3D 渲染不可用，已切换列表'));});
  return {setPapers,reset,resize,select:id=>{selected=id;schedule();},dispose:()=>{disposed=true;cancelAnimationFrame(frame);observer.disconnect();controls.dispose();group.traverse(o=>{o.geometry?.dispose();o.material?.dispose();});renderer.dispose();renderer.domElement.remove();labels.remove();}};
}
