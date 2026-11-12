# CaptchaLab AI Synthetic Dataset

Generated at: 2026-11-12T16:06:59.905925+00:00  
Base seed: `20261112`  
Total samples: **300**  

## Challenge families

| Family | Samples | What the solver must do |
|---|---:|---|
| `synthetic_text` | 100 | Transcribe a coloured, rotated five-character string |
| `synthetic_arithmetic` | 100 | Read a digit/operator/digit expression and calculate it |
| `synthetic_image_grid` | 100 | Classify controlled geometric shapes by colour and shape, then return 1-based cell positions |

The image-grid family uses a controlled vocabulary of six colours and three geometric shapes. It is a computer-vision benchmark, not a claim of general photograph recognition. None of these families reproduces a provider's production challenge or bypasses a provider widget.

Images are stored in `images/`. The ground-truth index is `labels.csv`.
