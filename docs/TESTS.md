# Тесты Hydra Panel

Прогон: `pytest` · 51 тест · все проходят · ~6 с

Каждый тест работает на изолированном `HYDRA_HOME` (временный каталог),
боевая база не затрагивается.

## Доступ

| Тест | Что проверяет |
|---|---|
| `test_root_redirects_to_dashboard` | `/` без авторизации → 302 на `/dashboard` |
| `test_dashboard_requires_auth` | `/dashboard` без сессии → 303 на `/login` |
| `test_login_rejects_wrong_password` | неверный пароль → 401 |
| `test_login_ok` | вход и рендер дашборда |
| `test_legacy_redirects` | старые `/clients` и `/inbounds` редиректят на новые разделы |

## Ноды и mTLS

| Тест | Что проверяет |
|---|---|
| `test_node_created_with_token` | создание ноды, одноразовая команда установки |
| `test_join_rejects_unknown_token` | чужой join-токен → 403 |
| `test_join_issues_certificate` | подпись CSR, выдача серта + CA, статус `online`, токен сгорает, повторный join → 403 |

## Подключения

| Тест | Что проверяет |
|---|---|
| `test_inbound_reality_generates_keys` | генерация X25519-пары и shortId для REALITY |
| `test_inbound_tls_and_hy2` | создание xhttp/tls, trojan/grpc, hysteria2 |

## Подписки

| Тест | Что проверяет |
|---|---|
| `test_v2ray_links_are_valid` | base64-список: vless/trojan/hysteria2, `pbk`+`sid`+`flow`, `type=xhttp&path&mode` |
| `test_client_detection` | Happ и v2RayTun — по настройке (Happ по умолчанию JSON Xray), Shadowrocket → text/plain; clash/mihomo/FlClash → YAML; SFA/sing-box → JSON |
| `test_clash_profile_parses` | YAML парсится, типы vless/trojan/hysteria2, url-test группа, `MATCH,PROXY`, reality-opts |
| `test_singbox_profile_parses` | JSON парсится, selector + outbounds, `route.final=proxy` |
| `test_subscription_headers` | `subscription-userinfo`, `profile-update-interval` |
| `test_forced_format` | `/clash`, `/singbox`, `/links` отдают нужный формат |
| `test_happ_json_profile` | Happ получает **массив** конфигов: «Автовыбор» с `proxy-1…N`, балансером и observatory, дальше по конфигу на сервер (`proxy`/`direct`/`block`, socks-in+http-in, реальный uuid, hysteria как `protocol: hysteria`) |
| `test_happ_singbox_setting_is_ignored` | сохранённый в базе `singbox` для Happ читается как `happ` и не ломает импорт |
| `test_happ_forced_format` | `/sub/<token>/happ` — принудительный JSON |
| `test_happ_routing_header` | Happ получает заголовок `routing: happ://routing/onadd/<base64>`, остальные клиенты — нет |
| `test_browser_gets_html_page` | браузер получает страницу подписки, а не base64 |
| `test_sub_page_marks_device` | iPhone получает пометку «ваше устройство» и нужную инструкцию первой |
| `test_subscription_preview` | проверка UA на странице «Подписка» показывает приложение и первую строку профиля |
| `test_app_format_override` | смена формата для mihomo в настройках меняет ответ подписки |
| `test_disabled_app_falls_back` | выключенное приложение не перехватывает UA |

## Маршрутизация

| Тест | Что проверяет |
|---|---|
| `test_routing_rule_reaches_profiles` | правило `suffix → direct` появляется в clash и sing-box, удаление убирает его |
| `test_routing_preset_and_block` | пресет «реклама» добавляет `REJECT` в clash и outbound `block` в sing-box |

## Трафик и лимиты

| Тест | Что проверяет |
|---|---|
| `test_traffic_accounting_and_limit` | накопление дельт, автоотключение при превышении, подписка → 403, пометка `auto-off` |
| `test_expired_client_blocked` | просроченный пользователь отключается |
| `test_extend_reenables` | продление возвращает доступ |
| `test_dashboard_periods` | график рендерится для day/month/year |
| `test_users_page_marks_expiring` | у пользователя с 3 днями остатка считается `days_left` |

## Конфиги движков

| Тест | Что проверяет |
|---|---|
| `test_xray_config_shape` | api+StatsService, policy `statsUserUplink`, `network=raw`, reality dest, fallbacks на 8080, email `uuid\|tag` |
| `test_hysteria_config` | listen, userpass, trafficStats secret, obfs salamander |
| `test_payload_excludes_disabled` | выключенный пользователь не попадает в конфиг ноды |

## Прочее

| Тест | Что проверяет |
|---|---|
| `test_api_key_auth` | `/api/*` без ключа → 401, с неверным → 401, с верным → 200 |
| `test_backup_and_restore` | архив содержит БД, настройки и `certs/`; восстановление откатывает изменения |
| `test_bots_page_and_save` | страница ботов и сохранение настроек |
| `test_engines_crud` | добавление, изменение формата и удаление движка |
| `test_decoy_page` | заглушка для fallback отдаётся |

## Оплата

| Тест | Что проверяет |
|---|---|
| `test_plans_and_payment_apply` | тариф из панели, зачёт платежа: срок продлён, трафик добавлен, повторный зачёт не дублирует |
| `test_payments_page_and_approve` | страница заявок и подтверждение оплаты кнопкой в панели |
| `test_payment_methods_depend_on_settings` | звёзды для XTR, карта только с токеном провайдера, ручной перевод только при включённых реквизитах |

## Устройства (HWID)

| Тест | Что проверяет |
|---|---|
| `test_hwid_devices_registered` | устройство пишется по `X-HWID` (платформа, модель), повторный запрос не плодит записи, заголовок `x-hwid-active` |
| `test_hwid_limit_blocks_new_device` | второй HWID сверх лимита → 403 + `x-hwid-max-devices-reached`, запрос без HWID → `x-hwid-not-supported` |
| `test_hwid_unlink_from_panel` | отвязка устройства из профиля пользователя |
| `test_hwid_payment_resets_devices` | оплата снимает привязки при включённой настройке |

## Уведомления

| Тест | Что проверяет |
|---|---|
| `test_notify_due_clients` | попадание в выборку по сроку, граница «за N дней», сухой прогон без отправки |

## Тексты бота

| Тест | Что проверяет |
|---|---|
| `test_bot_texts_editor` | редактор отдаёт страницу, правка сохраняется, переменные подставляются, сброс возвращает заводской текст |
| `test_bot_texts_unknown_var_survives` | неизвестная переменная не роняет отправку сообщения |

## Как запустить

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

Отдельный тест: `pytest tests/test_panel.py::test_join_issues_certificate -v`
