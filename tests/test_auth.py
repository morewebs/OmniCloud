from server import auth


def test_scrypt_roundtrip():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", h)
    assert not auth.verify_password("wrong", h)


def test_login_sets_session():
    auth.create_user("op", "pw123456", role="admin")
    result = auth.login("op", "pw123456")
    assert result is not None
    token, user = result
    assert user.role == "admin"
    assert auth.user_for_token(token).username == "op"


def test_login_rejects_bad_password():
    auth.create_user("op", "pw123456")
    assert auth.login("op", "nope") is None


def test_setup_closes_after_first_user():
    assert auth.user_count() == 0
    auth.create_user("first", "pw123456", role="admin")
    assert auth.user_count() == 1


def test_disabled_user_cannot_login():
    from server import db
    uid = auth.create_user("gone", "pw123456")
    with db.connect() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (uid,))
    assert auth.login("gone", "pw123456") is None
