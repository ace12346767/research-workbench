// Copy the pinned renderer and orbit controls into the offline desktop bundle.
const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
async function main(){
  const root=path.resolve(__dirname,'..');
  const source=path.join(root,'node_modules/three');
  const target=path.join(root,'assets/web/vendor/three');
  await fs.mkdir(target,{recursive:true});
  const files={'three.module.js':'build/three.module.js','three.core.js':'build/three.core.js','OrbitControls.js':'examples/jsm/controls/OrbitControls.js','LICENSE':'LICENSE'};
  const manifest={version:JSON.parse(await fs.readFile(path.join(source,'package.json'),'utf8')).version,files:{}};
  for(const [name,file] of Object.entries(files)){const data=await fs.readFile(path.join(source,file));await fs.writeFile(path.join(target,name),data);manifest.files[name]=crypto.createHash('sha256').update(data).digest('hex');}
  await fs.writeFile(path.join(target,'manifest.json'),JSON.stringify(manifest,null,2));
  console.log(manifest);
}
main().catch(e=>{console.error(e);process.exitCode=1;});
