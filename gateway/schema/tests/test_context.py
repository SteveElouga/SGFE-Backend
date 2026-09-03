"""Tests de l'extraction du token — header HTTP et connectionParams WebSocket."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from schema.context import AuthError, _token_from_connection_params, require_auth


def _info(connection_params: dict[str, str], header: str = "") -> MagicMock:
    """Fabrique un `info` GraphQL : requête sans header d'auth + connectionParams."""
    request = MagicMock()
    request.headers.get.return_value = header  # extract_token lit .headers.get("Authorization", "")
    info = MagicMock()
    info.context = {"request": request, "connection_params": connection_params}
    return info


class ConnectionParamsAuthTests(TestCase):
    @patch("schema.context.auth_client")
    def test_require_auth_lit_le_token_des_connection_params(self, mock_auth_client: MagicMock) -> None:
        """Sur une subscription WS (pas de header), le JWT est lu dans connectionParams."""
        mock_auth_client.validate_token.return_value = MagicMock(role="ADMIN", user_id="u-1")
        info = _info({"Authorization": "Bearer tok-123"})

        require_auth(info)

        mock_auth_client.validate_token.assert_called_once_with("tok-123")

    def test_token_from_connection_params_tolerant_aux_cles(self) -> None:
        self.assertEqual(_token_from_connection_params(_info({"authToken": "raw-token"})), "raw-token")
        self.assertEqual(_token_from_connection_params(_info({"token": "raw-token"})), "raw-token")
        self.assertEqual(_token_from_connection_params(_info({"authorization": "Bearer x"})), "x")

    def test_require_auth_sans_token_leve_autherror(self) -> None:
        with self.assertRaises(AuthError):
            require_auth(_info({}))
