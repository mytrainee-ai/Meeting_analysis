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
.action{display:inline-block;background:#246b49;color:#fff;padding:11px 15px;border-radius:9px;font-weight:700;margin:10px 0 22px}.list{display:grid;gap:11px}
.meeting{background:#fff;border:1px solid #e3e8e1;border-radius:13px;padding:17px;display:flex;justify-content:space-between;gap:14px;align-items:center}
.meeting strong{display:block;margin-bottom:5px;overflow-wrap:anywhere}.meta{font-size:12px;color:#7a877e}.badge{font-size:11px;background:#edf2ec;padding:6px 9px;border-radius:99px;color:#516257}
.empty{text-align:center;background:#fff;border:1px dashed #ccd6cc;border-radius:14px;padding:38px;color:#748077}button{border:0;background:none;color:#287148;cursor:pointer}form{display:inline}
</style></head><body><header><div class="brand">М · Встречи</div><div class="account"><span>__EMAIL__</span>__ADMIN__<form method="post" action="/logout"><button>Выйти</button></form></div></header>
<main><h1>Ваши встречи</h1><a class="action" href="/upload">+ Загрузить встречу</a><div id="meetings" class="list"><p class="empty">Загружаем список…</p></div></main>
<script>
const isAdmin=__IS_ADMIN__, labels={uploaded:'Загружено',processing_transcription:'Распознавание речи',processing_analysis:'Анализ расшифровки',completed:'Готово',failed_transcription:'Ошибка расшифровки',failed_analysis:'Ошибка анализа'};
fetch('/api/meetings').then(r=>{if(!r.ok)throw Error();return r.json()}).then(items=>{const root=document.querySelector('#meetings');root.replaceChildren();if(!items.length){const p=document.createElement('p');p.className='empty';p.textContent='Пока нет встреч. Загрузите первую запись.';root.append(p);return}for(const item of items){const card=document.createElement('article');card.className='meeting';const info=document.createElement('div');const title=document.createElement('strong');title.textContent=item.filename;const meta=document.createElement('div');meta.className='meta';meta.textContent=`${(item.size_bytes/1048576).toFixed(1)} МБ · ${Math.floor(item.duration_seconds/60)} мин · ${new Date(item.created_at).toLocaleString('ru-RU')}`;info.append(title,meta);const link=document.createElement('a');link.href=`/meetings/${item.id}`;link.className='badge';link.textContent=item.media_deleted_at?'Медиа удалено · '+(labels[item.status]||item.status):(labels[item.status]||item.status);card.append(info,link);if(isAdmin){const remove=document.createElement('button');remove.textContent='Удалить';remove.onclick=async()=>{if(!confirm(`Удалить встречу «${item.filename}» и её материалы?`))return;const response=await fetch(`/api/meetings/${item.id}`,{method:'DELETE'});if(response.ok)card.remove();else alert('Не удалось удалить встречу.')};card.append(remove)}root.append(card)}}).catch(()=>{document.querySelector('#meetings').textContent='Не удалось загрузить список встреч.'});
</script></body></html>"""
    return page.replace("__EMAIL__", escape(email)).replace("__ADMIN__", admin_link).replace("__IS_ADMIN__", "true" if is_admin else "false")


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
