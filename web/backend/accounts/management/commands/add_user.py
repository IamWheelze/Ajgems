"""Create a user in an organisation with roles. The password is typed at a hidden prompt, never on the command line.

    python manage.py add_user --username ada --organisation "Minefoot Ltd" --roles ml
    python manage.py add_user --username bola --organisation "Minefoot Ltd" --roles admin,gis

Privileged users (admin, ml) enrol multi-factor sign-in the first time they sign in (FR-001).
"""
import getpass

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organisation, Role, User


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, p):
        p.add_argument("--username", required=True); p.add_argument("--organisation", required=True)
        p.add_argument("--roles", required=True, help=f"comma-separated: {', '.join(Role.values)}")
        p.add_argument("--email", default="")

    def handle(self, username, organisation, roles, email, **_):
        roles = [r.strip() for r in roles.split(",") if r.strip()]
        bad = [r for r in roles if r not in Role.values]
        if bad:
            raise CommandError(f"unknown roles {bad}; choose from {Role.values}")
        if User.objects.filter(username=username).exists():
            raise CommandError(f"user {username} already exists")
        pw = getpass.getpass(f"Password for {username}: ")
        if len(pw) < 12 or pw != getpass.getpass("Again: "):
            raise CommandError("passwords must match and be at least 12 characters")
        org, made = Organisation.objects.get_or_create(name=organisation)
        User.objects.create_user(username, email=email, password=pw, organisation=org, roles=roles)
        self.stdout.write(f"created {username} in {org.name}{' (new organisation)' if made else ''} with roles {roles}")
