from app.services.synthetic import generate_arithmetic_challenge, generate_text_challenge
from app.services.ocr import solve_arithmetic_image, solve_text_image
from app.services.image_grid import generate_image_grid_challenge, solve_image_grid_challenge


def test_arithmetic_family_is_distinct_and_solves_seeded_samples():
    for seed in range(40):
        image, expected, _challenge_id, prompt = generate_arithmetic_challenge(91000 + seed)
        assert "calculate" in prompt.lower()
        result = solve_arithmetic_image(image)
        assert result["text"] == expected
        assert len(result["expression"]) == 3
        assert result["method"] == "opencv_glyph_recognition_plus_arithmetic_cpu"


def test_image_grid_family_solves_seeded_colour_shape_tasks():
    for seed in range(100):
        image, expected, _challenge_id, prompt = generate_image_grid_challenge(92000 + seed)
        result = solve_image_grid_challenge(image, prompt)
        assert result["text"] == expected
        assert result["selected_cells"] == ([int(value) for value in expected.split(",")] if expected else [])
        assert len(result["cell_classifications"]) == 9
        assert result["confidence"] is None  # no fabricated probability score


def test_each_synthetic_family_is_deterministic():
    text_a = generate_text_challenge(1234)
    text_b = generate_text_challenge(1234)
    math_a = generate_arithmetic_challenge(1234)
    math_b = generate_arithmetic_challenge(1234)
    grid_a = generate_image_grid_challenge(1234)
    grid_b = generate_image_grid_challenge(1234)
    assert text_a[0:2] == text_b[0:2]
    assert math_a[0:2] == math_b[0:2]
    assert math_a[3] == math_b[3]
    assert grid_a[0:2] == grid_b[0:2]
    assert grid_a[3] == grid_b[3]
