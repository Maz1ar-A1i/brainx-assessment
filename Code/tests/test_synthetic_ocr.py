from app.services.synthetic import generate_text_challenge
from app.services.ocr import solve_text_image


def test_generated_challenge_is_deterministic():
    a = generate_text_challenge(42)
    b = generate_text_challenge(42)
    assert a[1] == b[1]
    assert a[0] == b[0]


def test_solver_reads_seeded_visual_variations():
    for seed in range(25):
        image, expected, _ = generate_text_challenge(61000 + seed)
        result = solve_text_image(image)
        assert result["text"] == expected
        assert result["accepted_format"] is True
        assert result["method"] == "opencv_multifont_template_matching_cpu"


def test_corrupt_input_is_rejected():
    try:
        solve_text_image(b"not an image")
    except ValueError as exc:
        assert str(exc) == "invalid_image"
    else:
        raise AssertionError("invalid image should be rejected")
