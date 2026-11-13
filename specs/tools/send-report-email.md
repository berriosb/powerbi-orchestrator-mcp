# Spec: Tool `send_report_email`

> Envía reportes (Excel/PDF adjuntos + resumen verificado) por Microsoft Graph (`sendMail`) o SMTP (`STARTTLS`), integrando allowlist de dominios, confirmación explícita para envíos a mandante externo y redacción SHA-256 de emails en respuestas y bitácora.

**Scope:** `Tools.Write` (mutante, `dry_run=True` por defecto, auditada).

## Seguridad
- Exige `PBI_EMAIL_ALLOWED_DOMAINS` para envíos reales (`dry_run=False`).
- Exige `confirm_external_send=True` cuando `audience_profile="external_client"` o algún dominio difiere de `PBI_EMAIL_INTERNAL_DOMAINS`.
- Adjuntos acotados por `validate_safe_pbip_path` dentro de `PBI_WORKSPACE_ROOT` y límite total de 10 MB.
- Nunca retorna ni escribe direcciones de correo en claro (`<email_hash:sha256[:8]>`).
