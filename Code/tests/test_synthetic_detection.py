import io

from PIL import Image
from fastapi.testclient import TestClient

from app.main import app
from app.services.image_grid import generate_image_grid_challenge
from app.services.synthetic import generate_arithmetic_challenge, generate_text_challenge
from app.services.synthetic_detection import detect_synthetic_image_type


def test_auto_detects_each_controlled_synthetic_family():
    for seed in range(20):
        text_image, *_ = generate_text_challenge(93000 + seed)
        arithmetic_image, *_ = generate_arithmetic_challenge(94000 + seed)
        grid_image, *_ = generate_image_grid_challenge(95000 + seed)

        assert detect_synthetic_image_type(text_image)["challenge_type"] == "synthetic_text"
        assert detect_synthetic_image_type(arithmetic_image)["challenge_type"] == "synthetic_arithmetic"
        assert detect_synthetic_image_type(grid_image)["challenge_type"] == "synthetic_image_grid"


def test_auto_detection_returns_unsupported_for_unrelated_image():
    image = Image.new("RGB", (320, 240), (255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    result = detect_synthetic_image_type(output.getvalue())
    assert result["detected"] is False
    assert result["family"] == "unknown"
    assert result["supported"] is False


def test_detection_api_accepts_image_upload():
    image, *_ = generate_arithmetic_challenge(96001)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/detect/synthetic-image",
            files={"file": ("arithmetic.png", image, "image/png")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["detected"] is True
    assert body["challenge_type"] == "synthetic_arithmetic"


def test_detection_api_rejects_missing_file():
    with TestClient(app) as client:
        response = client.post("/api/v1/detect/synthetic-image")
    assert response.status_code == 422
