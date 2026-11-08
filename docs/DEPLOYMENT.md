# Публикация и эксплуатация пилота

## Текущий статус

Пилот опубликован по HTTPS на [app.traineeserver.ru](https://app.traineeserver.ru). Последняя документированная внешняя проверка `/health` вернула `200` 2026-11-07. Публикация настроена через GitHub Actions на push в `main`; владелец подтвердил `PILOT_DEPLOY_ENABLED=true` 2026-11-08.

При успешном запуске workflow релиз переключается на сервере, `meeting-analysis.service` перезапускается, затем проверяется `/health`; при ошибке скрипт возвращает предыдущий релиз. Проверяйте каждый запуск во вкладке **Actions** репозитория. Последние изменения считаются опубликованными только после успешного workflow.

## Среда пилота

- Одна VM Ubuntu 26.04.1 LTS: 1 vCPU, 2 ГБ RAM, 20 ГБ NVMe.
- Python 3.12.16 в venv `/opt/meeting-analysis/venv`; FFmpeg/FFprobe 8.0.1.
- Один Uvicorn worker через `meeting-analysis.service`; приложение слушает loopback `127.0.0.1:8000` за Nginx.
- SQLite и файлы: `/var/lib/meeting-analysis`; релизы: `/opt/meeting-analysis/releases`, активная версия: `/opt/meeting-analysis/current`.
- Секреты лежат только в `/etc/meeting-analysis/.env` с правами `0600`; они не входят в GitHub workflow и архив релиза.
- HTTPS обслуживается Nginx/Certbot. UFW разрешает входящий SSH, HTTP и HTTPS; Zabbix agent недоступен снаружи через firewall.
- База сервера отдельная. Локальные пользователи, приглашения, встречи и медиа при первом развёртывании не переносились.

## Автоматическое обновление

Workflow: `.github/workflows/deploy-pilot.yml`. Он реагирует на push в `main` и использует:

**GitHub repository variables**

- `PILOT_DEPLOY_ENABLED=true`
- `PILOT_DEPLOY_HOST=app.traineeserver.ru`
- `PILOT_DEPLOY_USER=meeting-deploy`

**GitHub repository secrets**

- `PILOT_SSH_PRIVATE_KEY` — приватный ключ deploy-пользователя.
- `PILOT_KNOWN_HOSTS` — проверенная строка host key для deploy-хоста.

Перед упаковкой workflow отказывается включать `data/`, `.env` и прочие `.env.*` файлы, кроме `.env.example`. Он не запускается на pull request и не выполняет тесты. Скрипт отказуется переключать релиз, если обнаруживает встречу со статусом `processing_*`; после завершения обработки deploy можно повторить. Релиз кратко перезапускает приложение.

Обычная публикация из PowerShell:

```powershell
git add .
git status
git commit -m "Краткое описание изменений"
git push origin main
```

Перед коммитом убедитесь, что `git status` не показывает `.env`, `data/` или другие секреты. Затем проверьте результат workflow во вкладке **Actions**.

## Проверка и диагностика на сервере

```bash
curl -fsS https://app.traineeserver.ru/health
sudo systemctl status meeting-analysis.service --no-pager
sudo journalctl -u meeting-analysis.service --since "30 minutes ago" --no-pager
sudo nginx -t
sudo ufw status verbose
```

Секреты не выводить в консоль, журналы или переписку. Для просмотра причины ошибки отдельной встречи используйте **«Просмотр логов»** в колонке статуса: приложение сохраняет ограниченные очищенные сведения FFmpeg или AI API. Журнал systemd может содержать технические метаданные обработки, но не должен содержать видео, аудио, транскрипт или ключи.

После изменения `/etc/meeting-analysis/.env` перезапустите службу:

```bash
sudo systemctl restart meeting-analysis.service
```

Перезапуск завершает активные входы и может прервать текущую обработку. Пользователям потребуется войти снова; неуспешную обработку можно запустить повторно.

## Ограничения и отложенные работы

- Фоновые задачи выполняются внутри одного процесса. Не увеличивать число workers и не создавать реплики приложения без перехода на устойчивую очередь задач.
- SQLite и медиа находятся на локальном диске VM.
- Резервное копирование для учебного пилота не настроено по решению владельца. Сбой или удаление VM может привести к потере данных. До хранения важных встреч настроить копирование SQLite/медиа и проверить восстановление.
- После 24 часов видео и временный аудиофайл удаляются; расшифровка и результаты остаются до удаления встречи.

Подробные продуктовые и серверные решения: [DECISIONS.md](DECISIONS.md). Текущее состояние проекта: [CURRENT_STATE.md](CURRENT_STATE.md).
