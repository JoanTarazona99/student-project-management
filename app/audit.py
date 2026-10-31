import json

from sqlalchemy import text


def audit(connection, actor_id, action, entity_type, entity_id, details=None):
    connection.execute(text("""
        INSERT INTO public.audit_events
            (actor_id, action, entity_type, entity_id, details)
        VALUES (:actor, :action, :kind, :id, CAST(:details AS jsonb))
    """), {"actor": actor_id, "action": action, "kind": entity_type,
             "id": entity_id, "details": json.dumps(details or {})})
