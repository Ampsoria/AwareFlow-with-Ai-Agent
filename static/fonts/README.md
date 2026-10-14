# Web font

Noto Sans Thai, variable weights 100–900, provided under the SIL Open Font License in `OFL.txt`.

Source: https://github.com/google/fonts/tree/main/ofl/notosansthai

`NotoSansThai-Variable.ttf` is the original font. The WOFF2 file is a lossless web-font conversion made with fontTools (no glyph or naming changes). The browser loads it from this app through Streamlit static file serving, without requesting a third-party font service.

The website theme is configured in `.streamlit/config.toml`; layout styles and navigation icons live in `src/awareflow/ui/theme.py`. Restart Streamlit after changing server or font configuration. Choose the custom application theme in Streamlit settings if a browser has an older theme override.
