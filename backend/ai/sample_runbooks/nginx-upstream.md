# Nginx: 502 Bad Gateway и upstream timed out

В логах nginx `upstream timed out (110: Connection timed out)` или
`connect() failed (111: Connection refused) while connecting to upstream`.

## connect() failed (111: Connection refused)

Приложение за nginx не слушает порт: упало или не успело запуститься.

```bash
sudo systemctl status gunicorn
sudo journalctl -u gunicorn -n 100 --no-pager
sudo ss -ltnp | grep 8000
```

Если сервис упал — смотреть причину в журнале (часто ошибка импорта или переменной окружения
после деплоя), исправить и `sudo systemctl restart gunicorn`.

## upstream timed out (110)

Приложение отвечает дольше `proxy_read_timeout` (по умолчанию 60 с).

1. Проверить загрузку: CPU, load average, память — все воркеры могут быть заняты.
2. Найти медленные запросы в логах приложения и в PostgreSQL (`log_min_duration_statement`).
3. Проверить, не исчерпан ли пул соединений к БД.
4. Временная мера — увеличить число воркеров Gunicorn (`--workers 2*CPU+1`).

Увеличивать `proxy_read_timeout` стоит только для действительно долгих операций
(выгрузки, отчёты) и только в отдельном `location`.

## Gunicorn: WORKER TIMEOUT

`[CRITICAL] WORKER TIMEOUT` значит, что воркер не ответил за `--timeout` секунд и был убит.
Причины те же, что у upstream timeout: медленные запросы к БД или внешним API.
Если сообщение сопровождается `was terminated due to signal 9` — проверьте OOM killer
(см. runbook «Нехватка памяти»).
