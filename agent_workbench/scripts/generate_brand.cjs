// Deterministic Mobius mesh; sharp rasterizes the editable SVG into app/Windows assets.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('sharp');
const root = path.resolve(__dirname, '../assets/web/brand');
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const sub=(a,b)=>a.map((v,i)=>v-b[i]);
function vertex(u,v) {
  const radius=1+v*Math.cos(u/2), x=radius*Math.cos(u), y=radius*Math.sin(u), z=v*Math.sin(u/2);
  const tilt=.58, spin=-.55;
  const yy=y*Math.cos(tilt)-z*Math.sin(tilt), zz=y*Math.sin(tilt)+z*Math.cos(tilt);
  return [x*Math.cos(spin)-yy*Math.sin(spin),x*Math.sin(spin)+yy*Math.cos(spin),zz];
}
function svg(small=false,mono=false) {
  const mesh=[],segments=180,strips=8,width=small?.52:.46;
  for(let i=0;i<segments;i++) for(let j=0;j<strips;j++) {
    const u=i/segments*Math.PI*2, next=(i+1)/segments*Math.PI*2;
    const a=-width+2*width*j/strips,b=-width+2*width*(j+1)/strips;
    const p=[vertex(u,a),vertex(next,a),vertex(next,b),vertex(u,b)];
    const n=cross(sub(p[1],p[0]),sub(p[3],p[0]));
    const norm=Math.hypot(...n),light=Math.abs((n[0]*-.25+n[1]*-.35+n[2]*.9)/norm);
    const palette=[[17,191,165],[56,142,234],[239,120,142],[244,185,77]];
    const position=i/segments*palette.length,from=palette[Math.floor(position)],to=palette[(Math.floor(position)+1)%palette.length];
    const base=mono?[35,77,64]:from.map((value,k)=>value+(to[k]-value)*(position%1));
    const shade=.68+.32*light;
    const color=`rgb(${base.map(v=>Math.round(v*shade)).join(',')})`;
    mesh.push({depth:p.reduce((s,v)=>s+v[2],0)/4, path:`<path d="M${p.map(v=>`${(256+v[0]*164).toFixed(2)},${(256+v[1]*164).toFixed(2)}`).join('L')}Z" fill="${color}" stroke="${color}" stroke-width=".65"/>`});
  }
  mesh.sort((a,b)=>a.depth-b.depth);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="AgentWorkbench Mobius ring">${mesh.map(m=>m.path).join('')}</svg>`;
}
async function main(){
  await fs.mkdir(root,{recursive:true});
  const source=svg();
  await fs.writeFile(path.join(root,'mobius.svg'),source);
  await fs.writeFile(path.join(root,'mobius-mono.svg'),svg(false,true));
  await sharp(Buffer.from(source)).resize(512,512).png().toFile(path.join(root,'mobius.png'));
  const sizes=[16,24,32,48,64,128,256];
  const images=[];
  for(const size of sizes) {
    // Classic DIB entries remain compatible with WinForms/System.Drawing icon decoding.
    const pixels=await sharp(Buffer.from(svg(size<=32))).resize(size,size).ensureAlpha().raw().toBuffer();
    const maskStride=Math.ceil(size/32)*4;
    const dib=Buffer.alloc(40+size*size*4+maskStride*size);
    dib.writeUInt32LE(40,0);dib.writeInt32LE(size,4);dib.writeInt32LE(size*2,8);
    dib.writeUInt16LE(1,12);dib.writeUInt16LE(32,14);dib.writeUInt32LE(size*size*4,20);
    for(let y=0;y<size;y++) for(let x=0;x<size;x++) {
      const src=(y*size+x)*4,dst=40+((size-1-y)*size+x)*4;
      dib[dst]=pixels[src+2];dib[dst+1]=pixels[src+1];dib[dst+2]=pixels[src];dib[dst+3]=pixels[src+3];
      if(!pixels[src+3]) dib[40+size*size*4+(size-1-y)*maskStride+(x>>3)] |= 128>>(x%8);
    }
    images.push(dib);
  }
  const header=Buffer.alloc(6+16*sizes.length);header.writeUInt16LE(1,2);header.writeUInt16LE(sizes.length,4);
  let offset=header.length;
  for(let i=0;i<sizes.length;i++){
    const pos=6+16*i;header[pos]=header[pos+1]=sizes[i]===256?0:sizes[i];header.writeUInt16LE(1,pos+4);header.writeUInt16LE(32,pos+6);
    header.writeUInt32LE(images[i].length,pos+8);header.writeUInt32LE(offset,pos+12);offset+=images[i].length;
  }
  await fs.writeFile(path.resolve(root,'../../AgentWorkbench.ico'),Buffer.concat([header,...images]));
  console.log('Generated Mobius SVG, monochrome SVG, PNG and seven-size ICO');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
