'use strict';
// Inject only UXP storage into the real client; no HTTP peer or native API.
const fs=require('node:fs'),path=require('node:path');
const {create}=require('../plugin/connection.js');
const directory=process.argv[2];
const entry=name=>({name,isFile:true,read:async()=>fs.readFileSync(path.join(directory,name),'utf8'),
  write:async raw=>fs.writeFileSync(path.join(directory,name),raw,'utf8')});
const folder={nativePath:directory,getEntries:async()=>fs.readdirSync(directory).map(entry),
  createFile:async(name,options)=>{fs.closeSync(fs.openSync(path.join(directory,name),options.overwrite?'w':'wx'));return entry(name);}};
global.fetch=async()=>{throw new Error('Transport is forbidden during enrollment');};
create({storage:{localFileSystem:{getDataFolder:async()=>folder}}},JSON.parse(process.argv[3])).connect().then(
  ()=>{process.exitCode=1;},error=>{if(error.code!=='INSTALLATION_PENDING'){process.stderr.write(error.code);process.exitCode=1;}});
