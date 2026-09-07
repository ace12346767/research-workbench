import * as THREE from 'three';

export function createMobius(host, onFailure) {
  const renderer = new THREE.WebGLRenderer({alpha:true, antialias:true, preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0xffffff, 0);
  const canvas = renderer.domElement;
  canvas.setAttribute('aria-hidden','true');
  host.append(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, .1, 30);
  camera.position.set(0, 0, 7.8);
  const group = new THREE.Group();
  group.rotation.set(.6, .25, -.4);
  scene.add(group);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x77948a, 2.5));
  const key = new THREE.DirectionalLight(0xffffff, 3.2); key.position.set(-3, 5, 6); scene.add(key);
  const rim = new THREE.DirectionalLight(0xc8e8f2, 2); rim.position.set(4, -2, 3); scene.add(rim);
  const position = [], colors = [], indices = [], segments = 180, across = 18;
  const palette=['#11bfa5','#388eea','#ef788e','#f4b94d'].map(color=>new THREE.Color(color));
  const flowingColor=(u,phase=0)=>{const p=((u/(Math.PI*2)+phase)%1+1)%1*palette.length;return palette[Math.floor(p)].clone().lerp(palette[(Math.floor(p)+1)%palette.length],p%1);};
  const point = (u,v) => [(1.55 + v*Math.cos(u/2))*Math.cos(u), (1.55 + v*Math.cos(u/2))*Math.sin(u), v*Math.sin(u/2)];
  for(let i=0;i<=segments;i++) for(let j=0;j<=across;j++) {
    const u=i/segments*Math.PI*2, v=(j/across-.5)*1.02;
    position.push(...point(u,v));
    const color=flowingColor(u);
    colors.push(color.r,color.g,color.b);
    if(i<segments && j<across) {const a=i*(across+1)+j,b=a+across+1;indices.push(a,b,a+1,b,b+1,a+1);}
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(position,3));
  geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));
  geometry.setIndex(indices);geometry.computeVertexNormals();
  const material=new THREE.MeshStandardMaterial({vertexColors:true,side:THREE.DoubleSide,metalness:.12,roughness:.46});
  group.add(new THREE.Mesh(geometry,material));
  // A Mobius strip has one continuous boundary, traversed over two revolutions.
  const boundary=[];for(let i=0;i<=360;i++) boundary.push(...point(i/360*Math.PI*4,.51));
  const edgeGeometry=new THREE.BufferGeometry();edgeGeometry.setAttribute('position',new THREE.Float32BufferAttribute(boundary,3));
  const edgeMaterial=new THREE.LineBasicMaterial({color:'#ffffff',transparent:true,opacity:.35});
  group.add(new THREE.Line(edgeGeometry,edgeMaterial));
  let disposed=false,active=false,frame=0,last=0,phase=0;
  function draw() {if(disposed || !host.clientWidth || !host.clientHeight)return;renderer.render(scene,camera);canvas.dataset.rendered='true';}
  function tick(now) {
    frame=0;if(!active || disposed)return;
    if(now-last>=1000/30) {
      phase+=Math.min((now-last)/1000,.05);last=now;
      group.rotation.y=.25+Math.sin(phase*.18)*.32;
      group.rotation.z=-.4+phase*.075;
      const attribute=geometry.getAttribute('color');
      for(let i=0;i<=segments;i++) {const color=flowingColor(i/segments*Math.PI*2,phase*.025);for(let j=0;j<=across;j++)attribute.setXYZ(i*(across+1)+j,color.r,color.g,color.b);}
      attribute.needsUpdate=true;
      draw();
    }
    frame=requestAnimationFrame(tick);
  }
  function setActive(value) {
    if(active===value)return;
    active=value;canvas.dataset.animating=String(value);
    cancelAnimationFrame(frame);frame=0;last=performance.now();
    if(active)frame=requestAnimationFrame(tick);
  }
  function resize() {
    if(disposed || !host.clientWidth || !host.clientHeight)return;
    renderer.setSize(host.clientWidth,host.clientHeight,false);
    camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();draw();
  }
  const observer=new ResizeObserver(resize);observer.observe(host);
  canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();if(disposed)return;setActive(false);onFailure();});
  resize();host.classList.add('mobius-ready');canvas.dataset.animating='false';
  return {setActive,dispose(){if(disposed)return;setActive(false);disposed=true;observer.disconnect();geometry.dispose();material.dispose();edgeGeometry.dispose();edgeMaterial.dispose();renderer.dispose();renderer.forceContextLoss();canvas.remove();host.classList.remove('mobius-ready');}};
}
