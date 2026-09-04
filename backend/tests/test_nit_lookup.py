import asyncio
from app.services.fel_service import validate_nit_modulo_11, lookup_nit_sat


def test_validate_nit_modulo_11():
    # 1. Casos CF válidos
    is_valid, exp, act = validate_nit_modulo_11("CF")
    assert is_valid is True
    assert exp == "CF"

    is_valid, exp, act = validate_nit_modulo_11("C/F")
    assert is_valid is True

    # 2. NIT matemáticamente válido calculado
    # 372918-4 (Suma: 3*7 + 7*6 + 2*5 + 9*4 + 1*3 + 8*2 = 128 % 11 = 7; 11-7 = 4)
    is_valid, exp, act = validate_nit_modulo_11("372918-4")
    assert is_valid is True
    assert exp == "4"
    assert act == "4"

    # 3. NIT matemáticamente inválido
    is_valid, exp, act = validate_nit_modulo_11("372918-9")
    assert is_valid is False
    assert exp == "4"
    assert act == "9"

    # 4. NIT vacío o no numérico
    is_valid, exp, act = validate_nit_modulo_11("")
    assert is_valid is False

    is_valid, exp, act = validate_nit_modulo_11("ABCDE-F")
    assert is_valid is False


def test_lookup_nit_sat_cf():
    res = asyncio.run(lookup_nit_sat("CF"))
    assert res["found"] is True
    assert res["valid"] is True
    assert res["name"] == "CONSUMIDOR FINAL"
    assert res["nit"] == "CF"


def test_lookup_nit_sat_known():
    # Cervecería Centro Americana: 33333-3
    res = asyncio.run(lookup_nit_sat("33333-3"))
    assert res["found"] is True
    assert res["valid"] is True
    assert "CERVECERIA CENTRO AMERICANA" in res["name"]


def test_lookup_nit_sat_invalid_modulo11():
    # NIT con dígito incorrecto (1234567-5 en lugar de 1234567-9)
    res = asyncio.run(lookup_nit_sat("1234567-5"))
    assert res["found"] is False
    assert res["valid"] is False
    assert "no es válido según el algoritmo SAT" in res["error"]
