#!/usr/bin/env python3
"""Merge bot.* user-facing strings into locale JSON catalogs (en/es/pt/ru)."""
from __future__ import annotations

import json
from pathlib import Path

STRINGS_DIR = Path(__file__).resolve().parent.parent / "Modules" / "i18n" / "strings"

BOT_EN: dict = {
    "command_perms": {
        "docs_title": "Command Permissions",
        "docs_description": (
            "Full reference: which slash commands use which **member** permissions, and what **bot** "
            "permissions features need. Use `/command_perms edit` (Manage Server) to type any slash "
            "command name and set requirements."
        ),
        "docs_quick_links": "Quick links",
        "docs_footer": "Per-guild overrides apply after the next sync; defaults ship with the bot.",
        "docs_button": "Open permissions doc",
        "list_empty": "No overrides in this server. Commands use bot defaults (see `/command_perms docs`).",
        "list_title": "Command permission overrides",
        "edit_no_match": "No matching slash command. Use the name Discord shows (e.g. `help`, `call create`, `modules toggle`).",
        "edit_custom_required": "For **Custom**, fill `custom_permissions` (comma-separated permission names).",
        "edit_reset_default": "Reset **`/{command}`** to the bot default.",
        "edit_removed": "Removed custom rules for **`/{command}`** (no CoffeeCord member-permission gate).",
        "edit_set": "**`/{command}`** now requires: {rule}",
        "deny_manage_guild": "You need **Manage Server** (or **Administrator**) to change command permissions.",
        "deny_guild_only": "This command can only be used in a server.",
        "deny_member_resolve": "Could not resolve your member permissions.",
        "deny_mod_combo": (
            "You need **Manage Roles**, **Moderate Members**, **Manage Server**, "
            "or **Administrator** to use this command."
        ),
        "deny_everyone": "You cannot use this command.",
        "deny_need_all": "You need all of these permissions: **{permissions}**.",
        "deny_no_permission": "You do not have permission to use this command.",
    },
    "support": {
        "title": "Support Coffeecord! 💙",
        "description": (
            "Click the button below to support us via Ko-fi.\n\n"
            "✅ Link your Discord account to Ko-fi and buy us a coffee or membership to support us!\n\n"
            "**Perks:**\n"
            "- `Supporter` role!\n"
            "- Access to a private channel!\n"
            "- Early access to new features!\n"
            "- Play GIFs in your leveling card!\n"
            "- Unlimited translations!\n\n"
            "**How to activate your perks:**\n"
            "1. Click **Support us via Ko-fi** below and complete your donation or membership.\n"
            "2. Come back to Discord and run `/kofi link email:you@example.com` — use the same email you used on Ko-fi.\n"
            "3. Your perks activate instantly. Run `/kofi status` to confirm.\n\n"
            "Need help? Join the support server: {invite}"
        ),
        "kofi_button": "Support us via Ko-fi",
    },
    "kofi": {
        "invalid_email": "Please provide a valid email address.",
        "link_success": "Linked. Claimed **{count}** existing Ko-fi payment(s) for `{email}`.",
        "link_queued": (
            "Linked `{email}`. You are **#{position}** in line for the next Ko-fi payment on that email. "
            "Perks activate when a payment arrives."
        ),
        "claim_success": "Claimed **{count}** unlinked payment(s) for `{email}`.",
        "claim_none": "No unlinked payments found for `{email}`.",
        "not_supporter": "You are not marked as a supporter yet.",
        "status_title": "Ko-fi Supporter Status",
        "field_tier": "Tier",
        "field_last_payment": "Last Payment",
        "field_total_usd": "Total USD",
        "add_success": "{user} marked as an active supporter.",
        "remove_no_record": "No supporter record exists for that user.",
        "remove_success": "Supporter status disabled for {user}.",
    },
    "poll": {
        "choose_channel": "📊 Choose a channel to send the poll:",
        "select_placeholder": "Select a channel to send the poll to...",
        "title": "📊 Poll",
        "footer": "Poll ends in {minutes} minute(s).",
        "sent": "✅ Poll sent to {channel}",
        "ended": "🛑 Poll ended! Thanks for voting.",
        "clear_reactions_denied": "⚠️ I do not have permission to clear reactions.",
    },
    "verify": {
        "wrong_user": "This isn't for you.",
        "role_assign_failed": "I cannot assign the role (missing permission or role hierarchy).",
        "button_success": "✅ You've been verified!",
        "button_prompt": "Press the button below to verify yourself:",
        "button_label": "Verify Me ✅",
        "code_success": "✅ Verification successful!",
        "code_wrong": "❌ Incorrect code. Try again later.",
        "code_modal_title": "Enter Verification Code",
        "code_input_label": "Verification Code",
        "code_input_placeholder": "Enter your 4-digit code",
        "color_wrong_session": "This isn't your verification session.",
        "color_wrong": "❌ Incorrect color. Try again!",
        "color_not_your_session": "❌ Not your session.",
        "invalid_method": "❌ Invalid verification method.",
        "log_button": "✅ {user} verified via **Simple Button**.",
        "log_code": "✅ {user} verified via **Keypad Code**.",
        "log_color": "✅ {user} verified via **Color Buttons**.",
    },
    "call": {
        "guild_not_found": "Guild not found.",
        "user_not_found": "Could not resolve your user for this command.",
        "create_forbidden": (
            "I need **Manage Channels** (and permission to manage that category) to create call channels. "
            "Ask an admin to grant the bot **Manage Channels**."
        ),
        "create_failed": "Could not create the call channel: {error}",
        "created": (
            "📞 Call created: {channel}\n"
            "Invited users must use **/call join** to join.\n"
            "{password_line}"
        ),
        "password_line": "🔑 Password: `{password}`",
        "dm_invite": (
            "📞 **{host} is calling you!**\n"
            "➡️ Use **/call join** to join: {channel}\n"
            "{password_line}"
        ),
        "join_guild_only": "Use this in a server (could not resolve guild or user).",
        "join_not_found": "This call does not exist or has expired.",
        "join_wrong_password": "Incorrect password.",
        "join_hierarchy": "The bot is not high enough in the role hierarchy (or lacks permissions).",
        "join_success": "✅ You joined the call in {channel}.",
        "not_host": "You aren't the host of any active call.",
        "channel_gone": "Call channel no longer exists.",
        "end_success": "Call ended.",
        "promote_success": "Promoted {user} to call host.",
        "add_success": "Added {user} to the call.",
        "remove_success": "Removed {user} from the call.",
        "remove_not_in_call": "That user isn't in the call.",
        "no_active_host": "You don't currently host any active call.",
    },
    "timers": {
        "invalid_format": "Invalid format. Use like 10s, 5m, 2h, or 1d.",
        "reminder_set": "⏰ Reminder set for <t:{timestamp}:R>",
        "timer_started": "⏳ Timer #{id} ends at <t:{timestamp}:R>",
        "timer_dm": "⏰ Timer #{id} is up!",
        "no_timers": "You have no active timers.",
        "timer_list": "Your active timers: {list}",
        "timer_cancelled": "Timer #{id} cancelled.",
        "timer_not_found": "Timer not found or not yours.",
    },
    "optout": {
        "optout_success": "You will no longer receive staff DMs from this bot.",
        "optin_success": "You have opted back in to receiving staff DMs.",
    },
    "say": {
        "choose_channel": "Select a channel to send a message:",
        "sent": "Message sent to {channel}.",
    },
    "dm_cmd": {
        "choose_member": "Choose a user to DM",
        "sent": "DM sent to {user}.",
        "user_not_found": "User not found.",
    },
    "uninstall": {
        "confirm_title": "☕ Confirm Uninstall",
        "confirm_body": "This will remove Coffeecord channels, roles, and data from this server. Type **CONFIRM** to proceed.",
        "cancelled": "Uninstall cancelled.",
        "wrong_confirm": "Confirmation text did not match. Uninstall cancelled.",
        "started": "Uninstall started…",
        "complete": "Uninstall complete.",
    },
    "nickname": {
        "no_permission": "I don't have permission to change my nickname.",
        "success": "Nickname changed to `{name}`.",
    },
    "purge": {
        "no_permission": "I don't have permission to delete messages in that channel.",
        "failed": "Failed to purge: {error}",
        "stopped": "Stopping purge — finishing the current batch, then halting…",
        "complete": "Deleted **{count}** message(s).",
    },
    "autorole_legacy": {
        "no_roles": "No autoroles to remove.",
    },
    "ticket_io": {
        "invalid_signature": "Invalid or expired ticket export signature.",
        "import_success": "Ticket imported into {channel}.",
        "export_failed": "Could not export ticket: {error}",
    },
    "applications": {
        "toggled": "Toggled staff applications!",
        "choose_delete": "Select question to delete:",
    },
    "debug": {
        "commands_printed": "Registered commands printed to console.",
    },
}

# Spanish / Portuguese / Russian: translate bot section (abbreviated script fills from EN + overrides)
BOT_ES = {
    "command_perms": {
        "docs_title": "Permisos de comandos",
        "docs_description": (
            "Referencia completa: qué permisos de **miembro** usa cada comando y qué permisos del **bot** "
            "necesitan las funciones. Usa `/command_perms edit` (Gestionar servidor) para cambiar requisitos."
        ),
        "docs_quick_links": "Enlaces rápidos",
        "docs_footer": "Las anulaciones por servidor aplican tras el próximo sync; los valores predeterminados vienen con el bot.",
        "docs_button": "Abrir documentación de permisos",
        "list_empty": "No hay anulaciones en este servidor. Los comandos usan los valores predeterminados del bot (ver `/command_perms docs`).",
        "list_title": "Anulaciones de permisos de comandos",
        "edit_no_match": "No hay comando que coincida. Usa el nombre que muestra Discord (p. ej. `help`, `call create`).",
        "edit_custom_required": "Para **Personalizado**, rellena `custom_permissions` (nombres separados por comas).",
        "edit_reset_default": "Restablecido **`/{command}`** al valor predeterminado del bot.",
        "edit_removed": "Eliminadas reglas personalizadas para **`/{command}`**.",
        "edit_set": "**`/{command}`** ahora requiere: {rule}",
        "deny_manage_guild": "Necesitas **Gestionar servidor** (o **Administrador**) para cambiar permisos de comandos.",
        "deny_guild_only": "Este comando solo puede usarse en un servidor.",
        "deny_member_resolve": "No se pudieron resolver tus permisos de miembro.",
        "deny_mod_combo": (
            "Necesitas **Gestionar roles**, **Moderar miembros**, **Gestionar servidor** "
            "o **Administrador** para usar este comando."
        ),
        "deny_everyone": "No puedes usar este comando.",
        "deny_need_all": "Necesitas todos estos permisos: **{permissions}**.",
        "deny_no_permission": "No tienes permiso para usar este comando.",
    },
    "support": {
        "title": "¡Apoya Coffeecord! 💙",
        "description": BOT_EN["support"]["description"].replace(
            "Need help? Join the support server: {invite}",
            "¿Necesitas ayuda? Únete al servidor de soporte: {invite}",
        ),
        "kofi_button": "Apóyanos en Ko-fi",
    },
    "kofi": {
        "invalid_email": "Proporciona una dirección de correo válida.",
        "link_success": "Vinculado. Reclamados **{count}** pago(s) de Ko-fi para `{email}`.",
        "link_queued": "Vinculado `{email}`. Eres **#{position}** en la cola para el próximo pago de Ko-fi en ese correo.",
        "claim_success": "Reclamados **{count}** pago(s) no vinculados para `{email}`.",
        "claim_none": "No se encontraron pagos no vinculados para `{email}`.",
        "not_supporter": "Aún no estás marcado como supporter.",
        "status_title": "Estado de supporter Ko-fi",
        "field_tier": "Nivel",
        "field_last_payment": "Último pago",
        "field_total_usd": "Total USD",
        "add_success": "{user} marcado como supporter activo.",
        "remove_no_record": "No existe registro de supporter para ese usuario.",
        "remove_success": "Estado de supporter desactivado para {user}.",
    },
    "poll": {
        "choose_channel": "📊 Elige un canal para enviar la encuesta:",
        "select_placeholder": "Selecciona un canal para la encuesta...",
        "title": "📊 Encuesta",
        "footer": "La encuesta termina en {minutes} minuto(s).",
        "sent": "✅ Encuesta enviada a {channel}",
        "ended": "🛑 ¡Encuesta terminada! Gracias por votar.",
        "clear_reactions_denied": "⚠️ No tengo permiso para borrar reacciones.",
    },
    "verify": {
        "wrong_user": "Esto no es para ti.",
        "role_assign_failed": "No puedo asignar el rol (faltan permisos o jerarquía de roles).",
        "button_success": "✅ ¡Has sido verificado!",
        "button_prompt": "Pulsa el botón de abajo para verificarte:",
        "button_label": "Verificarme ✅",
        "code_success": "✅ ¡Verificación exitosa!",
        "code_wrong": "❌ Código incorrecto. Inténtalo más tarde.",
        "code_modal_title": "Introduce el código de verificación",
        "code_input_label": "Código de verificación",
        "code_input_placeholder": "Introduce tu código de 4 dígitos",
        "color_wrong_session": "Esta no es tu sesión de verificación.",
        "color_wrong": "❌ Color incorrecto. ¡Inténtalo de nuevo!",
        "color_not_your_session": "❌ No es tu sesión.",
        "invalid_method": "❌ Método de verificación no válido.",
        "log_button": "✅ {user} verificado mediante **Botón simple**.",
        "log_code": "✅ {user} verificado mediante **Código numérico**.",
        "log_color": "✅ {user} verificado mediante **Botones de color**.",
    },
    "call": {
        "guild_not_found": "Servidor no encontrado.",
        "user_not_found": "No se pudo resolver tu usuario para este comando.",
        "create_forbidden": (
            "Necesito **Gestionar canales** para crear canales de llamada. "
            "Pide a un admin que conceda **Gestionar canales** al bot."
        ),
        "create_failed": "No se pudo crear el canal de llamada: {error}",
        "created": (
            "📞 Llamada creada: {channel}\n"
            "Los invitados deben usar **/call join** para unirse.\n"
            "{password_line}"
        ),
        "password_line": "🔑 Contraseña: `{password}`",
        "dm_invite": (
            "📞 **¡{host} te está llamando!**\n"
            "➡️ Usa **/call join** para unirte: {channel}\n"
            "{password_line}"
        ),
        "join_guild_only": "Usa esto en un servidor (no se pudo resolver servidor o usuario).",
        "join_not_found": "Esta llamada no existe o ha expirado.",
        "join_wrong_password": "Contraseña incorrecta.",
        "join_hierarchy": "El bot no está lo suficientemente alto en la jerarquía de roles (o le faltan permisos).",
        "join_success": "✅ Te uniste a la llamada en {channel}.",
        "not_host": "No eres el anfitrión de ninguna llamada activa.",
        "channel_gone": "El canal de llamada ya no existe.",
        "end_success": "Llamada terminada.",
        "promote_success": "{user} promovido a anfitrión de la llamada.",
        "add_success": "{user} añadido a la llamada.",
        "remove_success": "{user} eliminado de la llamada.",
        "remove_not_in_call": "Ese usuario no está en la llamada.",
        "no_active_host": "Actualmente no eres anfitrión de ninguna llamada activa.",
    },
    "timers": {
        "invalid_format": "Formato no válido. Usa 10s, 5m, 2h o 1d.",
        "reminder_set": "⏰ Recordatorio programado para <t:{timestamp}:R>",
        "timer_started": "⏳ Temporizador #{id} termina <t:{timestamp}:R>",
        "timer_dm": "⏰ ¡Temporizador #{id} terminado!",
        "no_timers": "No tienes temporizadores activos.",
        "timer_list": "Tus temporizadores activos: {list}",
        "timer_cancelled": "Temporizador #{id} cancelado.",
        "timer_not_found": "Temporizador no encontrado o no es tuyo.",
    },
    "optout": {
        "optout_success": "Ya no recibirás MD del staff de este bot.",
        "optin_success": "Te has vuelto a suscribir a MD del staff.",
    },
    "say": {"choose_channel": "Selecciona un canal para enviar un mensaje:", "sent": "Mensaje enviado a {channel}."},
    "dm_cmd": {
        "choose_member": "Elige un usuario para MD",
        "sent": "MD enviado a {user}.",
        "user_not_found": "Usuario no encontrado.",
    },
    "uninstall": {
        "confirm_title": "☕ Confirmar desinstalación",
        "confirm_body": "Esto eliminará canales, roles y datos de Coffeecord de este servidor. Escribe **CONFIRM** para continuar.",
        "cancelled": "Desinstalación cancelada.",
        "wrong_confirm": "El texto de confirmación no coincide. Desinstalación cancelada.",
        "started": "Desinstalación iniciada…",
        "complete": "Desinstalación completada.",
    },
    "nickname": {"no_permission": "No tengo permiso para cambiar mi apodo.", "success": "Apodo cambiado a `{name}`."},
    "purge": {
        "no_permission": "No tengo permiso para borrar mensajes en ese canal.",
        "failed": "Error al purgar: {error}",
        "stopped": "Deteniendo purga — terminando el lote actual…",
        "complete": "Eliminados **{count}** mensaje(s).",
    },
    "autorole_legacy": {"no_roles": "No hay autoroles que eliminar."},
    "ticket_io": {
        "invalid_signature": "Firma de exportación de ticket no válida o expirada.",
        "import_success": "Ticket importado en {channel}.",
        "export_failed": "No se pudo exportar el ticket: {error}",
    },
    "applications": {"toggled": "¡Solicitudes de staff alternadas!", "choose_delete": "Selecciona pregunta a eliminar:"},
    "debug": {"commands_printed": "Comandos registrados impresos en consola."},
}

BOT_PT = {
    "command_perms": {
        "docs_title": "Permissões de comandos",
        "list_empty": "Nenhuma substituição neste servidor. Os comandos usam os padrões do bot.",
        "list_title": "Substituições de permissões de comandos",
        "deny_no_permission": "Você não tem permissão para usar este comando.",
        "deny_guild_only": "Este comando só pode ser usado em um servidor.",
    },
    "poll": {
        "choose_channel": "📊 Escolha um canal para enviar a enquete:",
        "title": "📊 Enquete",
        "sent": "✅ Enquete enviada em {channel}",
        "ended": "🛑 Enquete encerrada! Obrigado por votar.",
    },
    "verify": {
        "wrong_user": "Isto não é para você.",
        "button_success": "✅ Você foi verificado!",
        "code_success": "✅ Verificação bem-sucedida!",
        "code_wrong": "❌ Código incorreto. Tente novamente mais tarde.",
    },
    "call": {
        "join_not_found": "Esta chamada não existe ou expirou.",
        "join_wrong_password": "Senha incorreta.",
        "not_host": "Você não é o host de nenhuma chamada ativa.",
        "channel_gone": "Canal de chamada não existe mais.",
    },
    "kofi": {"invalid_email": "Forneça um endereço de e-mail válido.", "not_supporter": "Você ainda não é marcado como apoiador."},
    "optout": {
        "optout_success": "Você não receberá mais DMs da equipe deste bot.",
        "optin_success": "Você voltou a receber DMs da equipe.",
    },
}

BOT_RU = {
    "command_perms": {
        "docs_title": "Права команд",
        "list_empty": "На этом сервере нет переопределений. Команды используют настройки бота по умолчанию.",
        "list_title": "Переопределения прав команд",
        "deny_no_permission": "У вас нет прав на эту команду.",
        "deny_guild_only": "Эту команду можно использовать только на сервере.",
    },
    "poll": {
        "choose_channel": "📊 Выберите канал для опроса:",
        "title": "📊 Опрос",
        "sent": "✅ Опрос отправлен в {channel}",
        "ended": "🛑 Опрос завершён! Спасибо за голос.",
    },
    "verify": {
        "wrong_user": "Это не для вас.",
        "button_success": "✅ Вы прошли верификацию!",
        "code_success": "✅ Верификация успешна!",
        "code_wrong": "❌ Неверный код. Попробуйте позже.",
    },
    "call": {
        "join_not_found": "Этот звонок не существует или истёк.",
        "join_wrong_password": "Неверный пароль.",
        "not_host": "Вы не являетесь хостом активного звонка.",
        "channel_gone": "Канал звонка больше не существует.",
    },
    "kofi": {"invalid_email": "Укажите действительный адрес электронной почты.", "not_supporter": "Вы ещё не отмечены как supporter."},
    "optout": {
        "optout_success": "Вы больше не будете получать ЛС от staff этого бота.",
        "optin_success": "Вы снова подписаны на ЛС от staff.",
    },
}


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _merge_locale(locale: str, bot_data: dict) -> None:
    path = STRINGS_DIR / f"{locale}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    existing = data.get("bot", {})
    if locale == "en":
        merged = bot_data
    else:
        merged = _deep_merge(BOT_EN, bot_data)
        merged = _deep_merge(merged, bot_data)
    data["bot"] = _deep_merge(existing, merged)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"merged bot section into {locale}.json")


def main() -> None:
    _merge_locale("en", BOT_EN)
    _merge_locale("es", BOT_ES)
    _merge_locale("pt", _deep_merge(BOT_EN, BOT_PT))
    _merge_locale("ru", _deep_merge(BOT_EN, BOT_RU))


if __name__ == "__main__":
    main()
