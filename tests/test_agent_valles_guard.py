import pytest
from api.agent.safety import contains_explicit_verdict, REFUSAL_MESSAGE


@pytest.mark.parametrize("text", [
    "Deberías comprar BTC ahora.",
    "Yo compraría esta moneda.",
    "La mejor opción es ADA.",
    "Pon el 10% de tu capital.",
    "Invierte $500 acá.",
    "Esta moneda va a subir.",
    "Te conviene entrar.",
    "Vale la pena comprarla.",
])
def test_explicit_verdict_caught(text):
    assert contains_explicit_verdict(text) is True


@pytest.mark.parametrize("text", [
    "Se mueve poco: su franja es de un 4% de su precio.",
    "El precio ya giró 3 veces en ese piso.",
    "Hay equipo público con sus fuentes. La decisión es tuya.",
    "No te digo si comprar; te leo los hechos.",
    "",
])
def test_legitimate_fact_reads_pass(text):
    assert contains_explicit_verdict(text) is False


def test_refusal_message_is_doctrinal():
    assert "decisión es tuya" in REFUSAL_MESSAGE.lower()
