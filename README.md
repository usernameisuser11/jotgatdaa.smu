# noticesmu-main

학술정보관은 Render에서 실시간 크롤링이 아니라 **JSON 캐시 파일**을 읽도록 바꾼 버전입니다.

## 실행
```bash
pip install -r requirements.txt
python app.py
```

## 학술정보관 캐시 갱신
로컬 PC에서만 갱신한 뒤, 생성된 `cache/library_seoul.json`, `cache/library_cheonan.json`을 같이 배포하면 됩니다.

```bash
python update_library_cache.py
```

## Render 팁
- Start Command: `gunicorn app:app`
- Health Check Path: `/health`
- 학술정보관 데이터 확인: `/cache-status`
