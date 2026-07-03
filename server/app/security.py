import uuid as uuid_lib
import jwt
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional

from app.models import Player


class TokenManager:
    """Управление JWT-токенами.

    Zero Trust:
    - Каждый токен имеет уникальный jti (JWT ID) для отзыва.
    - Blacklist хранит отозванные jti.
    - Токен привязан к (game_id, player_id).

    Payload:
    {
      "jti": "uuid-...",         # уникальный ID токена (для отзыва)
      "sub": "X" | "O",          # player_id
      "game_id": "uuid-...",     # ID игры
      "iat": 1234567890,         # issued at
      "exp": 1234567890 + TTL    # expires
    }
    """

    def __init__(self, secret: str, algorithm: str = "HS256", expire_seconds: int = 3600):
        self.secret = secret
        self.algorithm = algorithm
        self.expire_seconds = expire_seconds
        self._blacklist: dict[str, float] = {}  # jti → timestamp отзыва
        self._lock = Lock()

    def create_token(self, game_id: str, player_id: Player) -> str:
        """Создаёт JWT с уникальным jti."""
        now = datetime.utcnow()
        jti = str(uuid_lib.uuid4())
        payload = {
            "jti": jti,
            "sub": player_id.value,
            "game_id": game_id,
            "iat": now,
            "exp": now + timedelta(seconds=self.expire_seconds),
        }
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def decode_token(self, token: str) -> Optional[dict]:
        """Декодирует и проверяет JWT. Проверяет blacklist."""
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except jwt.PyJWTError:
            return None

        jti = payload.get("jti")
        if jti and self._is_revoked(jti):
            return None

        return payload

    def revoke_token(self, jti: str) -> bool:
        """Отзывает токен по jti. Возвращает True, если отозван."""
        with self._lock:
            if jti in self._blacklist:
                return False
            self._blacklist[jti] = datetime.utcnow().timestamp()
            return True

    def _is_revoked(self, jti: str) -> bool:
        with self._lock:
            return jti in self._blacklist

    def cleanup_blacklist(self, max_age_seconds: float = 86400.0):
        """Удаляет из blacklist записи старше max_age_seconds."""
        now = datetime.utcnow().timestamp()
        cutoff = now - max_age_seconds
        with self._lock:
            for jti, ts in list(self._blacklist.items()):
                if ts < cutoff:
                    del self._blacklist[jti]

    def blacklist_size(self) -> int:
        with self._lock:
            return len(self._blacklist)