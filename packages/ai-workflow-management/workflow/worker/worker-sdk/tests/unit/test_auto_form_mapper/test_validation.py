from worker_sdk.layer1_domain.auto_form_mapper.validation import (
    optional_string,
    required,
    required_email,
    required_number,
    required_string,
    url_validation,
)


# --- required ---

def test_required():
    rules = required()
    assert len(rules) == 1
    assert rules[0].method == "required"


def test_required_to_dict():
    d = [r.to_dict() for r in required()]
    assert d == [{"method": "required"}]


# --- required_string ---

def test_required_string_defaults():
    rules = required_string()
    assert len(rules) == 3
    assert rules[0].method == "required"
    assert rules[1].method == "min"
    assert rules[1].value == 1
    assert rules[2].method == "max"
    assert rules[2].value == 255


def test_required_string_custom():
    rules = required_string(min_length=5, max_length=50)
    assert rules[1].value == 5
    assert rules[2].value == 50
    assert "5" in rules[1].message
    assert "50" in rules[2].message


# --- required_email ---

def test_required_email():
    rules = required_email()
    assert len(rules) == 2
    assert rules[0].method == "required"
    assert rules[1].method == "pattern"
    assert "email" in rules[1].message.lower()


# --- required_number ---

def test_required_number_no_bounds():
    rules = required_number()
    assert len(rules) == 1
    assert rules[0].method == "required"


def test_required_number_with_bounds():
    rules = required_number(min_val=0, max_val=100)
    assert len(rules) == 3
    assert rules[0].method == "required"
    assert rules[1].method == "gte"
    assert rules[1].value == 0
    assert rules[2].method == "lte"
    assert rules[2].value == 100


def test_required_number_integer():
    rules = required_number(integer=True)
    assert len(rules) == 2
    assert rules[0].method == "required"
    assert rules[1].method == "int"


def test_required_number_all_options():
    rules = required_number(min_val=1, max_val=10, integer=True)
    assert len(rules) == 4


# --- optional_string ---

def test_optional_string_defaults():
    rules = optional_string()
    assert len(rules) == 1
    assert rules[0].method == "max"
    assert rules[0].value == 255


def test_optional_string_custom():
    rules = optional_string(max_length=500)
    assert rules[0].value == 500


# --- url_validation ---

def test_url_validation():
    rules = url_validation()
    assert len(rules) == 2
    assert rules[0].method == "required"
    assert rules[1].method == "pattern"
    assert "URL" in rules[1].message
