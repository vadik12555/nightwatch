# Celery worker упал (systemd failed)

Алерт «Сервис celery-worker в состоянии failed» или в логах
`Cannot connect to redis://...: Connection refused`.

## Диагностика

```bash
sudo systemctl status celery-worker
sudo journalctl -u celery-worker -n 100 --no-pager
redis-cli -h 127.0.0.1 ping
```

## Cannot connect to redis: Connection refused

Брокер недоступен.

1. Проверить, запущен ли Redis: `sudo systemctl status redis-server`.
2. Если Redis упал из-за памяти — смотреть `maxmemory` и runbook «Нехватка памяти».
3. Запустить Redis, затем воркер: `sudo systemctl restart redis-server celery-worker`.

## Воркер падает сразу после старта

Чаще всего ошибка импорта после деплоя или отсутствующая переменная окружения.
Запустить вручную от пользователя сервиса и посмотреть traceback:

```bash
sudo -u app /opt/app/venv/bin/celery -A config worker -l info
```

## Профилактика

В юните systemd: `Restart=on-failure` и `RestartSec=10`, чтобы краткие сбои брокера
не оставляли воркер выключенным.
