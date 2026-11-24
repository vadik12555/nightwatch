# Заканчивается место на диске

Срабатывает алерт «Заканчивается место на диске» или в логах `No space left on device`.
Приоритет: высокий — при 100% падают PostgreSQL, Docker и запись логов.

## Диагностика

Найти, что занимает место:

```bash
df -h
sudo du -xh / --max-depth=2 2>/dev/null | sort -rh | head -20
sudo journalctl --disk-usage
docker system df
```

Проверить удалённые, но ещё открытые файлы (место не освобождается, пока процесс жив):

```bash
sudo lsof +L1 | sort -k7 -rn | head
```

## Быстрое освобождение места

1. Сжать журнал systemd до 500 МБ: `sudo journalctl --vacuum-size=500M`.
2. Удалить старые образы и остановленные контейнеры Docker: `docker system prune -af` (не трогает volume'ы).
3. Почистить кэш apt: `sudo apt-get clean`.
4. Если место держит удалённый файл из `lsof +L1` — перезапустить процесс, который его держит.

Не удаляйте вручную файлы из `/var/lib/postgresql` и `/var/lib/docker`.

## PostgreSQL: No space left on device

Если PostgreSQL упал с `could not extend file ... No space left on device`:

1. Освободить место по шагам выше, не трогая каталог данных.
2. Проверить, не разросся ли WAL: `sudo du -sh /var/lib/postgresql/*/main/pg_wal`.
   Частая причина — неактивный replication slot: `SELECT slot_name, active FROM pg_replication_slots;`.
3. Запустить PostgreSQL: `sudo systemctl start postgresql` и проверить журнал.

## Профилактика

- Ротация логов через logrotate (`/etc/logrotate.d/`), лимит journald `SystemMaxUse=1G`.
- Алерт NightWatch на диск > 80% дольше 5 минут, чтобы успеть до 100%.
