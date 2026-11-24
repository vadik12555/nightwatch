# Подбор паролей по SSH

В логах sshd много `Failed password for invalid user` или `Invalid user ... from`.

## Насколько это опасно

Фоновый перебор паролей идёт на любой публичный сервер — это нормально. Опасно, если:
- разрешён вход по паролю (`PasswordAuthentication yes`);
- есть успешные входы с незнакомых IP: `grep "Accepted" /var/log/auth.log`.

## Что сделать

1. Отключить вход по паролю и для root в `/etc/ssh/sshd_config`:

```bash
PasswordAuthentication no
PermitRootLogin prohibit-password
```

   затем `sudo sshd -t && sudo systemctl reload ssh`. Перед этим убедитесь, что вход по ключу работает.
2. Включить fail2ban для sshd: `sudo apt install fail2ban` и `sudo fail2ban-client status sshd`.
3. По возможности ограничить доступ к 22 порту firewall'ом или VPN.

## Если вход был успешным с чужого IP

Считать сервер скомпрометированным: изолировать, сменить ключи и пароли, проверить
`~/.ssh/authorized_keys`, cron и systemd-юниты на посторонние записи.
