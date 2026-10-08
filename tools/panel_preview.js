/* Explicit synthetic fixture, only served by panel_preview.py; never in the CCX. */
const previewView=ContentriumView.install(document), get=id=>document.getElementById(id);
get('boot-status').className='notice';
get('boot-status').querySelector('p').textContent='UI 검토용 예시 · 실제 Premiere 연결 아님';
get('refresh').style.display='none';
get('project-name').textContent='Contentrium Podcast · EP.01';
get('sequence-info').textContent='01:02:18 · 29.97 fps · 예시 시퀀스';
get('track-count').textContent='3 VIDEO / 2 AUDIO';
get('connection').textContent='디자인 미리보기';
get('status').textContent='트랙·화자·컷·검토 화면을 확인할 수 있습니다.';
get('action-readiness').textContent='UI 검토용 예시 · 분석과 편집을 실행하지 않습니다.';
get('version').textContent='DESIGN PREVIEW';
get('cameras').innerHTML='';
const cameras=[['V1','CAM A · 진행자','speaker','A'],['V2','CAM B · 게스트','speaker','B'],['V3','CAM C · 전체샷','wide','A, B']];
for(const [id,name,role,covered] of cameras){
  const row=document.createElement('div');row.className='camera-row';
  row.innerHTML='<div class="track-title"><span class="track-badge">'+id+'</span><span class="source-title">'+name+'</span><span class="track-meta">1 CLIP</span></div><div class="row"><label>트랙 역할<select><option value="speaker">화자 카메라</option><option value="wide">전체샷</option><option value="two-shot">투샷</option><option value="protected">보호 트랙</option></select></label><label>보이는 화자<input value="'+covered+'"></label></div>';
  row.querySelector('select').value=role;get('cameras').appendChild(row);
}
get('microphones').innerHTML='';
for(const [id,name,speaker] of [['A1','진행자 마이크','A'],['A2','게스트 마이크','B']]){
  const row=document.createElement('div');row.className='source-row';
  row.innerHTML='<div class="track-title"><span class="track-badge audio">'+id+'</span><span class="source-title">'+name+'</span><input type="checkbox" checked aria-label="'+name+' 분석"></div><div class="row"><label>화자 ID<input value="'+speaker+'"></label><label>채널<select><option>1 · Mono</option><option>2</option></select></label></div>';get('microphones').appendChild(row);
}
get('speaker-mapping').innerHTML='';
for(const [id,name,track] of [['A','진행자','V1'],['B','게스트','V2']]){
  const row=document.createElement('div');row.className='source-row';
  row.innerHTML='<div class="track-title"><span class="track-badge">'+id+'</span><span class="source-title">'+name+'</span><span class="track-meta">예시 화자</span></div><label>이름<input value="'+name+'"></label><label>카메라<select>'+cameras.map(c=>'<option value="'+c[0]+'">'+c[0]+' · '+c[1]+'</option>').join('')+'</select></label><button class="full" disabled>▶ 단독 발화 듣기</button>';row.querySelector('select').value=track;get('speaker-mapping').appendChild(row);
}
for(const id of ['start-camera','reserve-camera'])get(id).innerHTML=cameras.map(c=>'<option value="'+c[0]+'">'+c[0]+' · '+c[1]+'</option>').join('');
get('reserve-camera').value='V3';
get('models').textContent='Silero · 준비됨 (예시)\nCommunity-1 · 준비됨 (예시)';
get('model-status').textContent='실제 분석 결과는 Premiere 연동 후 표시합니다.';
get('update-info').textContent='미리보기에서는 업데이트를 실행하지 않습니다.';
get('cache-info').textContent='4.2 GB / 20 GB (예시)';
get('segments').innerHTML='';
for(const [range,camera,note] of [['00:00 – 00:04','V3','시작 전체샷'],['00:04 – 00:12','V1','화자 A'],['00:12 – 00:19','V2','화자 B'],['00:19 – 00:22','V3','동시 발화'],['00:22 – 00:31','V1','화자 A']]){
  const row=document.createElement('div');row.className='segment-row';row.innerHTML='<span>'+range+'</span><span>'+camera+' · '+note+'</span>';row.onclick=()=>{get('status').textContent='예시 구간 '+range+' · 실제 타임라인은 이동하지 않습니다.';};get('segments').appendChild(row);
}
get('cut-count').textContent='5';get('review-count').textContent='1';
get('timeline').innerHTML='<div class="timeline"><div style="flex:4"></div><div style="flex:8"></div><div style="flex:7"></div><div style="flex:3"></div><div style="flex:9"></div></div>';
get('reviews').innerHTML='<div class="review-row">00:19 – 00:22 · 겹친 목소리 확인 (예시)</div>';
for(const id of ['analyze','plan','apply','sync','apply-sync','read-project','read-selection','create-input','save-settings','load-settings','install-model','open-model-provider','save-resources','prune-cache','check-update','recover-update','update','add-override'])get(id).disabled=true;
