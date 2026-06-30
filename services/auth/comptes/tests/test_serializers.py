from django.test import TestCase

from comptes.models import Role, User
from comptes.serializers import user_to_payload, user_to_response


class SerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="agent2",
            email="agent2@example.com",
            password="secret123",
            role=Role.AGENT,
            phone_number="+237690000010",
        )

    def test_user_to_payload(self):
        payload = user_to_payload(self.user)
        self.assertEqual(
            payload,
            {
                "user_id": str(self.user.id),
                "username": "agent2",
                "email": "agent2@example.com",
                "phone_number": "+237690000010",
                "role": Role.AGENT,
                "is_active": True,
            },
        )

    def test_user_to_response(self):
        response = user_to_response(self.user)
        self.assertEqual(response["user_id"], str(self.user.id))
        self.assertEqual(response["username"], "agent2")
        self.assertEqual(response["phone_number"], "+237690000010")
        self.assertEqual(response["created_at"], self.user.created_at.isoformat())
