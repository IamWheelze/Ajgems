"""Register a finished minefoot G2 run in the model registry (FR-101).

    python manage.py register_model --run ../../data/runs/g2_hn --user <ml username>
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from registry.services import RegistryError, register


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, p):
        p.add_argument("--run", required=True); p.add_argument("--user", required=True); p.add_argument("--name")

    def handle(self, run, user, name, **_):
        try:
            u = User.objects.get(username=user)
        except User.DoesNotExist:
            raise CommandError(f"no user {user}")
        if not u.has_role("ml"):
            raise CommandError("registration needs the ML role")
        try:
            mv = register(u, run, name)
        except RegistryError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"registered {mv.name} ({mv.state}), commit {mv.code_commit}"))
