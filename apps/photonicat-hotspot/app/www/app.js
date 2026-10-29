try{const host=parent!==window?parent:opener;document.documentElement.dataset.theme=host?(host.document.documentElement.classList.contains('dark')?'dark':'light'):(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light')}catch{}
const notice=document.querySelector('#notice'),container=document.querySelector('#devices');
const element=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node};
function labelled(title,input){const node=element('label',title);node.append(input);return node}
function message(text,error=false){notice.textContent=text;notice.classList.toggle('error',error)}
async function api(payload,path='./api'){const response=await fetch(path,payload?{method:'POST',headers:{'Content-Type':'application/json','X-Photonicat-Request':'1'},body:JSON.stringify(payload)}:{cache:'no-store'});let data;try{data=await response.json()}catch{throw Error('服务暂不可用，请稍后重试。')}if(!response.ok||!data.ok){const error=Error(data.error||'操作失败');error.status=response.status;throw error}return data}
function card(data){
 const section=element('article');section.className='card';const heading=element('div');heading.className='heading';const status=element('span',data.active?'运行中':data.enabled?'尚未启动':'已关闭');status.className='status'+(data.active?' on':'');heading.append(element('h2',data.title),status);section.append(heading);
 const form=element('form'),enabled=element('input');enabled.type='checkbox';enabled.checked=data.enabled;const toggle=labelled('开启热点',enabled);toggle.className='toggle';toggle.prepend(enabled);form.append(toggle);
 const ssid=element('input');ssid.value=data.ssid;ssid.maxLength=32;ssid.autocomplete='off';form.append(labelled('热点名称',ssid));
 const password=element('input');password.type='password';password.autocomplete='new-password';password.maxLength=63;password.placeholder=data.password_set?'已设置，留空保持不变':'8～63 位密码';form.append(labelled('热点密码',password));
 const band=element('select'),channel=element('select');for(const [value,title] of [['bg','2.4 GHz'],['a','5 GHz']])if(data.channels[value]?.length){const option=element('option',title);option.value=value;band.append(option)}
 band.value=[...band.options].some(o=>o.value===data.band)?data.band:band.options[0]?.value||'';
 function channels(){channel.replaceChildren();for(const value of data.channels[band.value]||[]){const option=element('option',String(value));option.value=String(value);channel.append(option)}if([...channel.options].some(o=>Number(o.value)===data.channel))channel.value=String(data.channel)}channels();band.addEventListener('change',channels);
 const row=element('div');row.className='row';row.append(labelled('频段',band),labelled('信道',channel));form.append(row);
 form.append(element('small',data.channels.a.length?'只显示当前驱动允许开启 AP 的非 DFS 信道。':'此网卡当前可使用 2.4 GHz 热点；固件尚未开放 5 GHz AP。'));
 const actions=element('div');actions.className='actions';const button=element('button','保存并应用');button.type='submit';button.disabled=!data.supports_ap||!band.options.length;actions.append(element('small',`热点网关 ${data.gateway}`),button);form.append(actions);
 form.addEventListener('submit',async event=>{event.preventDefault();if(enabled.checked&&!ssid.value.trim()){message('请填写热点名称。',true);return}button.disabled=true;button.textContent='正在应用…';message('正在应用设置；修改中的热点可能暂时断开。');try{const result=await api({operation:'apply',id:data.id,enabled:enabled.checked,ssid:ssid.value,password:password.value,band:band.value,channel:Number(channel.value)});render(result.devices);message('设置已保存。')}catch(error){if(error.status===401)await load();else message(error.message,true);button.disabled=false;button.textContent='保存并应用'}});
 section.append(form);return section;
}
function render(devices){container.replaceChildren(...devices.map(card));if(!devices.length)message('尚未检测到已适配的无线网卡。')}
async function load(){try{const data=await api();document.querySelector('#login').hidden=true;render(data.devices);if(data.devices.length)message('设置热点名称、密码和频段后，勾选开启并保存即可。')}catch(error){if(error.status===401){document.querySelector('#login').hidden=false;container.replaceChildren();message('请先验证管理员身份。')}else message(error.message,true)}}
document.querySelector('#login-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;try{await api({username:document.querySelector('#username').value,password:document.querySelector('#admin-password').value},'./login');document.querySelector('#admin-password').value='';await load()}catch(error){message(error.message,true)}finally{button.disabled=false}});
load();
