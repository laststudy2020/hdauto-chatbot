from urllib.parse import quote
from app.config import get_settings

settings = get_settings()


def get_location_response() -> str:
    lat = settings.COMPANY_LAT
    lng = settings.COMPANY_LNG
    name = quote(settings.COMPANY_NAME)

    naver_url = f"https://map.naver.com/p/directions/-/{lat},{lng},{name}/car"
    kakao_url = f"https://map.kakao.com/link/to/{settings.COMPANY_NAME},{lat},{lng}"

    return (
        f"{settings.COMPANY_NAME}\n\n"
        f"주소: {settings.COMPANY_ADDRESS}\n"
        f"전화: {settings.COMPANY_PHONE}\n"
        f"영업시간: {settings.COMPANY_HOURS}\n\n"
        f"길안내:\n"
        f"- 네이버 지도: {naver_url}\n"
        f"- 카카오 지도: {kakao_url}\n\n"
        f"방문 전 전화 확인 부탁드립니다!"
    )
