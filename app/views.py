"""Small server-rendered page templates for the local MVP."""

from __future__ import annotations

from html import escape


AUTH_CSS = """<style>
body{font:15px system-ui,sans-serif;background:#f4f6f2;color:#19251f;margin:0;min-height:100vh;display:grid;place-items:center}
main{background:#fff;border:1px solid #e3e8e1;border-radius:17px;padding:30px;width:min(410px,calc(100vw - 40px));box-shadow:0 18px 55px #253f2710}
h1{font-size:25px;letter-spacing:-.03em;margin:0 0 8px}p{font-size:13px;line-height:1.55;color:#718077}
label{display:block;font-size:12px;font-weight:650;margin:15px 0 6px}input{width:100%;padding:11px;border:1px solid #dce4da;border-radius:8px;font:inherit}
button{margin-top:19px;width:100%;padding:12px;border:0;border-radius:9px;background:#246b49;color:#fff;font-weight:700;cursor:pointer}
a{color:#287148}.error{color:#9c4339;background:#fff2ef;padding:10px;border-radius:8px}small{color:#7b887f}
</style>"""


def auth_page(title: str, intro: str, fields: str, action: str, submit: str, error: str = "") -> str:
    safe_title = escape(title)
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{safe_title} — Встречи</title>{AUTH_CSS}</head><body><main>"
        f"<h1>{safe_title}</h1><p>{escape(intro)}</p>{error_html}"
        f'<form method="post" action="{escape(action, quote=True)}">{fields}'
        f"<button>{escape(submit)}</button></form></main></body></html>"
    )


def setup_page(error: str = "") -> str:
    fields = (
        '<label for="email">Электронная почта администратора</label>'
        '<input id="email" name="email" type="email" autocomplete="username" required>'
        '<label for="password">Пароль, не короче 8 символов</label>'
        '<input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required>'
    )
    return auth_page(
        "Создание администратора",
        "Первоначальную учётную запись можно создать только с локального компьютера. Пароль хранится в виде стойкого хеша.",
        fields, "/setup", "Создать и войти", error,
    )


def login_page(error: str = "") -> str:
    fields = (
        '<label for="email">Электронная почта</label>'
        '<input id="email" name="email" type="email" autocomplete="username" required>'
        '<label for="password">Пароль</label>'
        '<input id="password" name="password" type="password" autocomplete="current-password" required>'
    )
    page = auth_page("Вход", "Войдите, чтобы открыть список встреч.", fields, "/login", "Войти", error)
    return page.replace("</form>", '</form><p>Есть приглашение? <a href="/register">Создать аккаунт</a></p>')


def register_page(error: str = "") -> str:
    fields = (
        '<label for="invite">Код приглашения</label>'
        '<input id="invite" name="invite_code" autocomplete="off" required>'
        '<label for="name">Имя</label>'
        '<input id="name" name="name" type="text" autocomplete="name" maxlength="120" required>'
        '<label for="email">Электронная почта</label>'
        '<input id="email" name="email" type="email" autocomplete="username" required>'
        '<label for="password">Пароль, не короче 8 символов</label>'
        '<input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required>'
    )
    return auth_page(
        "Регистрация", "Создайте учётную запись по активному приглашению администратора.",
        fields, "/register", "Зарегистрироваться", error,
    )


def dashboard_page(email: str, is_admin: bool) -> str:
    admin_link = '<a href="/admin">Приглашения и доступ</a>' if is_admin else ""
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Встречи</title>
<style>
body{font:15px system-ui,sans-serif;background:#f4f6f2;color:#19251f;margin:0}header{height:68px;background:#fff;border-bottom:1px solid #e4e9e2;display:flex;align-items:center;justify-content:space-between;padding:0 max(24px,calc((100% - 1000px)/2))}
main{max-width:1000px;margin:48px auto;padding:0 24px}.brand{font-weight:750}.account{display:flex;align-items:center;gap:18px;font-size:12px;color:#66746a}a{color:#287148;text-decoration:none}
.stats-panel{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr)) auto;gap:10px;align-items:stretch;margin:10px 0 25px}.stat-card{min-width:0;min-height:67px;background:#fff;border:1px solid #e7e8f1;border-radius:12px;box-shadow:0 3px 12px #283d6909;padding:10px;display:flex;align-items:center;gap:9px}.stat-icon{width:32px;height:32px;flex:0 0 32px;border-radius:10px;display:grid;place-items:center;font-size:18px;font-weight:750}.stat-icon.violet{background:#f0eaff;color:#8244fa}.stat-icon.green{background:#e9f9ee;color:#16a34a}.stat-icon.blue{background:#eaf2ff;color:#2470f3}.stat-icon.purple{background:#f3edff;color:#884bfb}.stat-icon.red{background:#fff0ef;color:#ef4444}.stat-value{display:block;font-size:20px;line-height:1.1;font-weight:750;color:#202438}.stat-label{display:block;margin-top:4px;font-size:10px;line-height:1.2;color:#596174}.action{display:flex;align-items:center;justify-content:center;gap:8px;background:linear-gradient(115deg,#7d39f5,#9845f4);color:#fff;padding:0 13px;border-radius:11px;font-size:11px;white-space:nowrap;font-weight:700}.action:hover{filter:brightness(.97)}.upload-arrow{font-size:16px}.upload-chevron{padding-left:6px;border-left:1px solid #ffffff55;font-size:14px}.stats-notice{grid-column:1/-1;font-size:11px;color:#9c4339}.list{display:grid;gap:11px}
.meeting-table{overflow-x:auto;margin-top:4px}.meeting-head,.meeting{display:grid;grid-template-columns:minmax(175px,1.5fr) 125px minmax(95px,1fr) 152px minmax(175px,1.45fr) 62px 80px 26px;gap:12px;align-items:center;min-width:898px}.meeting-table.admin .meeting-head,.meeting-table.admin .meeting{grid-template-columns:minmax(120px,1.4fr) minmax(70px,.8fr) minmax(98px,1fr) minmax(70px,.9fr) minmax(110px,1fr) minmax(120px,1.2fr) 48px 70px 22px;gap:8px;min-width:900px}.meeting-head{padding:0 15px 3px;font-size:10px;font-weight:700;color:#626c7e;white-space:nowrap}.meeting{background:#fff;border:1px solid #e3e8e1;border-radius:12px;padding:12px 15px;min-height:68px}.meeting-name{display:block;font-size:12px;font-weight:700;line-height:1.35;overflow-wrap:anywhere}.meeting-owner{font-size:11px;color:#56645a;overflow-wrap:anywhere}.meeting-date{font-size:11px;color:#606b7c;line-height:1.4;white-space:nowrap}.meeting-date small{display:block;font-size:10px;color:#87909d}.meeting-topic{font-size:10px;color:#6a50bc;background:#f2edff;border-radius:99px;padding:5px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.status-pill{justify-self:start;font-size:10px;padding:5px 8px;border-radius:99px;white-space:nowrap;background:#f2edff;color:#6a50bc}.status-pill.completed{background:#e9f8ee;color:#16843c}.status-pill.failed{background:#fff0ef;color:#d43a36}.status-pill.uploaded{background:#edf2ec;color:#59675e}.processing-status{min-width:0;display:flex;flex-direction:column;gap:7px}.processing-label{display:flex;align-items:center;gap:7px;font-size:10px;font-weight:650;color:#6a50bc;white-space:nowrap}.processing-spinner{width:11px;height:11px;flex:0 0 11px;border:2px solid #e6ddff;border-top-color:#8549fb;border-radius:50%;animation:spin .8s linear infinite}.process-steps{display:flex;gap:4px}.process-step{height:3px;flex:1;border-radius:3px;background:#e9e8ef}.process-step.done{background:#70c58d}.process-step.active{background:#8b49f5;animation:step-pulse 1.6s ease-in-out infinite alternate}@keyframes spin{to{transform:rotate(360deg)}}@keyframes step-pulse{to{opacity:.45}}.key-results{display:flex;align-items:center;gap:7px;white-space:nowrap}.result-count{display:inline-flex;align-items:center;gap:3px;font-size:10px;color:#465166}.result-count i{font-style:normal;font-size:12px;font-weight:750}.result-count.summary i{color:#59708e}.result-count.decisions i{color:#16a34a}.result-count.tasks i{color:#2563eb}.result-count.questions i{color:#884bfb}.result-count.errors i{color:#ef4444}.duration{font-size:11px;color:#252b3b;white-space:nowrap}.open-meeting{display:inline-flex;justify-content:center;align-items:center;border:1px solid #d9dced;border-radius:8px;padding:7px 9px;color:#263044;font-size:10px;font-weight:650;white-space:nowrap}.menu-placeholder{width:26px;height:30px;display:grid;place-items:center;color:#5f6879;font-size:21px;line-height:1}
.empty{text-align:center;background:#fff;border:1px dashed #ccd6cc;border-radius:14px;padding:38px;color:#748077}button{border:0;background:none;color:#287148;cursor:pointer}form{display:inline}
.status-cell{display:flex;flex-direction:column;align-items:flex-start;gap:5px}.log-button{padding:2px 0;font-size:10px;text-decoration:underline}.log-dialog{width:min(560px,calc(100% - 36px));border:1px solid #dfe5dd;border-radius:14px;padding:20px;box-shadow:0 16px 50px #15221833}.log-dialog::backdrop{background:#10181077}.log-dialog h2{margin:0 0 12px;font-size:18px}.log-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:55vh;overflow:auto;background:#f4f6f2;padding:12px;border-radius:8px;font:12px/1.5 ui-monospace,monospace}.log-dialog button{border:1px solid #d9e2d8;border-radius:7px;padding:7px 12px}
.panel-actions{display:flex;flex-direction:column;gap:8px}.danger-action{background:#fff;border:1px solid #e7c3bf;color:#a92f29}.danger-action:hover{background:#fff5f4}
@media(max-width:1080px){.stats-panel{grid-template-columns:repeat(3,minmax(0,1fr))}.action{min-height:58px;min-width:0}}
@media(max-width:560px){.stats-panel{grid-template-columns:repeat(2,minmax(130px,1fr));gap:8px}.stat-card{padding:9px;gap:7px}.stat-label{font-size:9px}.panel-actions{grid-column:1/-1;flex-direction:row}.panel-actions .action{flex:1;min-height:44px}}
</style></head><body><header><div class="brand">М · Встречи</div><div class="account"><span>__EMAIL__</span>__ADMIN__<form method="post" action="/logout"><button>Выйти</button></form></div></header>
<dialog class="log-dialog" id="logDialog"><h2>Диагностика обработки</h2><pre id="logContent">Загружаем…</pre><form method="dialog"><button>Закрыть</button></form></dialog>
<main><h1>Ваши встречи</h1><section class="stats-panel" aria-label="Сводка по встречам">
<div class="stat-card"><span class="stat-icon violet">▣</span><div><strong class="stat-value" data-stat="processed_meetings">—</strong><small class="stat-label">Встреч обработано</small></div></div>
<div class="stat-card"><span class="stat-icon green">✓</span><div><strong class="stat-value" data-stat="decisions">—</strong><small class="stat-label">Решений</small></div></div>
<div class="stat-card"><span class="stat-icon blue">☑</span><div><strong class="stat-value" data-stat="tasks">—</strong><small class="stat-label">Задач</small></div></div>
<div class="stat-card"><span class="stat-icon purple">?</span><div><strong class="stat-value" data-stat="open_questions">—</strong><small class="stat-label">Открытых вопросов</small></div></div>
<div class="stat-card"><span class="stat-icon red">⚠</span><div><strong class="stat-value" data-stat="errors">—</strong><small class="stat-label">Ошибок / проблем</small></div></div>
<div class="panel-actions"><a class="action" href="/upload"><span class="upload-arrow">⇧</span><span>Загрузить встречу</span><span class="upload-chevron">⌄</span></a><button type="button" class="action danger-action" id="deleteMyMeetings">Удалить встречи</button></div>
<small class="stats-notice" id="statsNotice" hidden>Не удалось загрузить сводку.</small></section><div class="meeting-table __ADMIN_CLASS__"><div class="meeting-head" role="row"><span>Название встречи</span><span class="owner-heading" hidden>Имя</span><span>Дата и время ↓</span><span>Тема</span><span>Статус</span><span>Ключевые результаты</span><span>Длительность</span><span></span><span></span></div><div id="meetings" class="list" role="rowgroup"><p class="empty">Загружаем список…</p></div></div></main>
<script>
const isAdmin=__IS_ADMIN__;
if(isAdmin)document.querySelector('.owner-heading').hidden=false;
const labels={uploaded:'Загружено',processing_audio_extraction:'Извлечение аудио',processing_transcription:'Распознавание речи',processing_analysis:'AI-анализ встречи',completed:'Готово',failed_transcription:'Ошибка расшифровки',failed_analysis:'Ошибка анализа'};
const processingStages=[['processing_audio_extraction','Извлечение аудио'],['processing_transcription','Распознавание речи'],['processing_analysis','AI-анализ встречи']];
let pollTimer=null, activeSeen=false, meetingsLoading=false;
function loadSummary(){return fetch('/api/meetings/summary').then(r=>{if(!r.ok)throw Error();return r.json()}).then(summary=>{for(const [key,value] of Object.entries(summary)){const target=document.querySelector(`[data-stat="${key}"]`);if(target)target.textContent=Number(value).toLocaleString('ru-RU')}}).catch(()=>{document.querySelector('#statsNotice').hidden=false})}
function makeCell(tag,className,text){const cell=document.createElement(tag);cell.className=className;if(text!==null)cell.textContent=text;return cell}
function durationText(seconds){const total=Math.floor(Number(seconds)||0);return `${Math.floor(total/60)}:${String(total%60).padStart(2,'0')}`}
function renderStatus(item){if(item.status.startsWith('processing_')){const index=Math.max(0,processingStages.findIndex(stage=>stage[0]===item.status));const box=makeCell('span','processing-status',null);box.setAttribute('role','cell');const label=makeCell('span','processing-label',null);const spinner=makeCell('span','processing-spinner','');spinner.setAttribute('aria-hidden','true');label.append(spinner,makeCell('span','',processingStages[index][1]));box.append(label);const bar=makeCell('span','process-steps',null);bar.setAttribute('role','progressbar');bar.setAttribute('aria-label',processingStages[index][1]);bar.setAttribute('aria-valuemin','1');bar.setAttribute('aria-valuemax','3');bar.setAttribute('aria-valuenow',String(index+1));for(let i=0;i<processingStages.length;i++){const step=makeCell('span','process-step'+(i<index?' done':i===index?' active':''),'');step.title=processingStages[i][1];bar.append(step)}box.append(bar);return box}const text=item.media_deleted_at?'Медиа удалено':(labels[item.status]||item.status);const failed=item.status.startsWith('failed_');const cell=makeCell('span','status-cell',null);cell.setAttribute('role','cell');const pill=makeCell('span','status-pill',text);pill.classList.add(failed?'failed':item.status==='completed'?'completed':'uploaded');cell.append(pill);if(failed&&item.error_message){const button=makeCell('button','log-button','Просмотр логов');button.type='button';button.addEventListener('click',()=>showMeetingLogs(item.id));cell.append(button)}return cell}
async function showMeetingLogs(id){const dialog=document.querySelector('#logDialog'),content=document.querySelector('#logContent');content.textContent='Загружаем…';dialog.showModal();try{const response=await fetch(`/api/meetings/${id}/logs`);const data=await response.json();if(!response.ok)throw Error(data.detail||'Не удалось получить диагностику.');const lines=[data.message||'Сообщение об ошибке отсутствует.'];if(data.details)lines.push(JSON.stringify(data.details,null,2));else lines.push('Техническая диагностика для этой старой попытки не сохранена. Запустите повторную обработку, чтобы при новом сбое сохранить подробности.');content.textContent=lines.join('\\n\\n')}catch(error){content.textContent=error.message}}
async function deleteMyMeetings(){const button=document.querySelector('#deleteMyMeetings');if(!confirm('Удалить все встречи, загруженные из вашей учётной записи? Будут удалены их видео и результаты. Учётные записи, приглашения и регистрации останутся без изменений. Это действие нельзя отменить.'))return;button.disabled=true;button.textContent='Удаляем…';try{const response=await fetch('/api/meetings/mine',{method:'DELETE'});const data=await response.json();if(!response.ok)throw Error(data.detail||'Не удалось удалить встречи.');if(data.deleted_count===0)alert('У вашей учётной записи нет встреч для удаления.');else alert(`Удалено встреч: ${data.deleted_count}`);location.reload()}catch(error){alert(error.message);button.disabled=false;button.textContent='Удалить встречи'}}
document.querySelector('#deleteMyMeetings').addEventListener('click',deleteMyMeetings);
function renderRows(items){const root=document.querySelector('#meetings');root.replaceChildren();if(!items.length){root.append(makeCell('p','empty','Пока нет встреч. Загрузите первую запись.'));return}for(const item of items){const row=document.createElement('article');row.className='meeting';row.setAttribute('role','row');const name=makeCell('span','meeting-name',item.title||item.filename);name.setAttribute('role','cell');row.append(name);if(isAdmin){const owner=makeCell('span','meeting-owner',item.owner_name||'—');owner.setAttribute('role','cell');row.append(owner)}const dateCell=makeCell('span','meeting-date',null);dateCell.setAttribute('role','cell');const dateValue=item.meeting_date?new Date(`${item.meeting_date}T12:00:00`):new Date(item.created_at);dateCell.append(makeCell('span','',dateValue.toLocaleDateString('ru-RU')));dateCell.append(makeCell('small','',new Date(item.created_at).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})));row.append(dateCell);const topic=makeCell('span','meeting-topic',item.topic||'—');topic.setAttribute('role','cell');row.append(topic);row.append(renderStatus(item));const resultCell=makeCell('span','key-results',null);resultCell.setAttribute('role','cell');const counts=item.key_results||{};for(const [key,icon,label,style] of [['summary','≡','Пункты содержания','summary'],['decisions','✓','Решения','decisions'],['tasks','☑','Задачи','tasks'],['questions','?','Открытые вопросы','questions'],['errors','⚠','Ошибки','errors']]){const count=makeCell('span','result-count '+style,null);count.title=label;count.setAttribute('aria-label',`${label}: ${Number(counts[key]||0)}`);count.append(makeCell('i','',icon),makeCell('span','',String(Number(counts[key]||0))));resultCell.append(count)}row.append(resultCell);const duration=makeCell('span','duration',durationText(item.duration_seconds));duration.setAttribute('role','cell');row.append(duration);const open=makeCell('a','open-meeting','Открыть');open.href=`/meetings/${item.id}`;open.setAttribute('role','cell');row.append(open);const more=makeCell('span','menu-placeholder','⋯');more.title='Действия встречи пока не подключены';more.setAttribute('aria-label','Меню встречи (заглушка)');more.setAttribute('role','cell');row.append(more);root.append(row)}}
async function loadMeetings(){if(meetingsLoading)return;meetingsLoading=true;try{const response=await fetch('/api/meetings');if(!response.ok)throw Error();const items=await response.json();renderRows(items);const processing=items.some(item=>item.status.startsWith('processing_'));if(processing){activeSeen=true;if(!pollTimer)pollTimer=setInterval(loadMeetings,2000)}else if(pollTimer){clearInterval(pollTimer);pollTimer=null;if(activeSeen)loadSummary();activeSeen=false}}catch{document.querySelector('#meetings').textContent='Не удалось загрузить список встреч.'}finally{meetingsLoading=false}}
loadSummary();loadMeetings();</script></body></html>"""
    return (page.replace("__EMAIL__", escape(email)).replace("__ADMIN__", admin_link)
            .replace("__IS_ADMIN__", "true" if is_admin else "false")
            .replace("__ADMIN_CLASS__", "admin" if is_admin else ""))


def admin_page(email: str) -> str:
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Доступ — Встречи</title>
<style>
body{font:14px system-ui,sans-serif;background:#f4f6f2;color:#19251f;margin:0}header{background:#fff;padding:18px max(24px,calc((100% - 900px)/2));border-bottom:1px solid #e4e9e2}main{max-width:900px;margin:35px auto;padding:0 24px}a{color:#287148}
section{background:#fff;border:1px solid #e3e8e1;border-radius:13px;padding:20px;margin:16px 0}button{border:0;border-radius:8px;background:#246b49;color:white;padding:9px 13px;cursor:pointer}
button.secondary{background:#edf2ec;color:#45574b}.row{display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid #edf0eb;gap:12px}small,.muted{color:#78847b}
#new-code{background:#edf7ee;padding:14px;border-radius:9px;overflow-wrap:anywhere;display:none}.error{color:#9c4339}
</style></head><body><header><a href="/">← Все встречи</a>　Администратор: __EMAIL__</header><main><h1>Управление доступом</h1>
<section><h2>Приглашения</h2><p class="muted">Код многоразовый и остаётся активен, пока вы его не отзовёте. Новый код показывается только при создании. Для регистрации отправьте код и ссылку /register.</p><button id="create">Создать приглашение</button><p id="new-code"></p><div id="invitations"></div></section>
<section><h2>Учётные записи</h2><div id="users"></div></section></main><script>
async function load(){const [ir,ur]=await Promise.all([fetch('/api/admin/invitations'),fetch('/api/admin/users')]);const invitations=await ir.json(),users=await ur.json();const irt=document.querySelector('#invitations');irt.replaceChildren();for(const inv of invitations){const row=document.createElement('div');row.className='row';const label=document.createElement('span');label.textContent=`${inv.created_at.slice(0,10)} · ${inv.last_used_at?'использовался':'ещё не использовался'} · ${inv.revoked_at?'отозван':'активен'}`;row.append(label);if(!inv.revoked_at){const b=document.createElement('button');b.className='secondary';b.textContent='Отозвать';b.onclick=async()=>{await fetch(`/api/admin/invitations/${inv.id}/revoke`,{method:'POST'});load()};row.append(b)}irt.append(row)}const urt=document.querySelector('#users');urt.replaceChildren();for(const user of users){const row=document.createElement('div');row.className='row';const label=document.createElement('span');label.textContent=`${user.email} · ${user.role==='admin'?'администратор':'пользователь'} · ${user.is_active?'активен':'заблокирован'}`;row.append(label);if(user.role!=='admin'){const b=document.createElement('button');b.className='secondary';b.textContent=user.is_active?'Заблокировать':'Разблокировать';b.onclick=async()=>{await fetch(`/api/admin/users/${user.id}/active`,{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:`active=${!user.is_active}`});load()};row.append(b)}urt.append(row)}}
document.querySelector('#create').onclick=async()=>{const response=await fetch('/api/admin/invitations',{method:'POST'});const data=await response.json();if(!response.ok){alert(data.detail||'Не удалось создать код.');return}const box=document.querySelector('#new-code');box.textContent=`Код приглашения (сохраните сейчас): ${data.code}`;box.style.display='block';load()};load();
</script></body></html>"""
    return page.replace("__EMAIL__", escape(email))
