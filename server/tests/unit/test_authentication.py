from proves_yamcs.authentication import AuthenticateFramer, SequenceStore


def test_authentication_known_vector_and_persistent_sequence(tmp_path):
    state = tmp_path / "sequence-number"
    framer = AuthenticateFramer("000102030405060708090a0b0c0d0e0f", state, spi=0)

    assert framer.frame(b"payload").hex() == (
        "0000000000007061796c6f616479779df7b41a86a276675b3cfbb8d1e4"
    )
    assert state.read_text(encoding="ascii") == "1"

    restarted = AuthenticateFramer("000102030405060708090a0b0c0d0e0f", state, spi=0)
    assert restarted.frame(b"payload")[2:6] == (1).to_bytes(4, "big")
    assert state.read_text(encoding="ascii") == "2"


def test_sequence_store_wraps_at_uint32(tmp_path):
    state = tmp_path / "sequence-number"
    state.write_text(str(0xFFFFFFFF), encoding="ascii")

    assert SequenceStore(state).next() == 0xFFFFFFFF
    assert state.read_text(encoding="ascii") == "0"
