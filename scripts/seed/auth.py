"""Seed de démo — comptes par rôle (auth). Idempotent (UUID déterministes).

Exécuté par scripts/seed_demo.sh via `manage.py shell -c`. Mot de passe commun : Demo1234!
Usernames préfixés `demo_` pour ne jamais entrer en collision avec des comptes réels.
"""

import uuid

from comptes.models import User

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "sgfe-demo-seed")
PWD = "Demo1234!"

# (username, role, phone_number unique, email)
COMPTES = [
    ("demo_admin", "ADMIN", "+237699000001", "demo_admin@sgfe.local"),
    ("demo_comptable", "COMPTABLE", "+237699000002", None),
    ("demo_superviseur", "SUPERVISEUR", "+237699000003", None),
    ("demo_agent", "AGENT", "+237699000004", None),
]

for username, role, phone, email in COMPTES:
    uid = uuid.uuid5(NS, f"user-{role.lower()}")
    user, _ = User.objects.get_or_create(
        id=uid,
        defaults={
            "username": username,
            "role": role,
            "phone_number": phone,
            "email": email,
        },
    )
    user.username = username
    user.role = role
    user.phone_number = phone
    user.email = email
    user.is_active = True
    user.set_password(PWD)
    user.save()
    print(f"  {role:12} {username:18} id={uid}")

print(f"OK — {len(COMPTES)} comptes de démo (mot de passe : {PWD})")
