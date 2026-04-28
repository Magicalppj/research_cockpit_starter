# Research Dashboard

## Current Focus

- **Stage:** `stage_text_encoder`
- **Problem:** `problem_event_text_weak`
- **Option:** `option_flan_t5_clap`

## Current Hypothesis

Event-level old/new text control can be improved by using FLAN-T5-XL token features plus CLAP audio-semantic anchors in the Semantic Ribbon branch.


## Open Risks

- Need train/inference parity for FLAN-T5 + CLAP timeline features.
- Need to verify whether CLAP anchor improves remove/replace under overlap.
- Need to avoid mixing legacy timeline inference with edit-program inference.

## Next Actions

- Regenerate timeline feature cache with FLAN-T5-XL token features.
- Add CLAP anchor projection to EventStateInitializer.
- Run FLAN-T5-only ablation on Audio Edit Dataset v2_150.
- Run FLAN-T5 + CLAP ablation and compare local edit following.

## Active Problems

- **Event-level text control is weak** (`problem_event_text_weak`): Gemma-based event features are likely insufficient for precise old/new edit control.
- **Remove and replace under overlap remain hard** (`problem_remove_overlap_weak`): Without explicit source decomposition, the model must learn local semantic suppression and preserve non-target components.

## Recent Decisions

- **Adopt FLAN-T5-XL + CLAP for event branch** (`proposed`): Keep LTX/Gemma global prompt path, use FLAN-T5-XL for event tokens and CLAP as event semantic anchor.
- **Do not make TTS add a main neural editing task** (`accepted`): TTS add can be handled by TTS + traditional mixing; the neural editor focuses on remove/replace/local rewriting.
- **Use unified edit-program formulation** (`accepted`): Treat text-to-audio generation as add edits from silence, and reference editing as local semantic rewriting.