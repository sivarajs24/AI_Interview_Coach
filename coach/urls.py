from django.urls import path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("interview", views.interview_page, name="interview_page"),
    path("report/<str:session_id>", views.report_page, name="report_page"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("api/start-session", views.start_session, name="start_session"),
    path("api/upload-response", views.upload_response, name="upload_response"),
    path("api/finish-interview", views.finish_interview, name="finish_interview"),
    path("api/report/<str:session_id>", views.get_report, name="get_report"),
    path("api/report/<str:session_id>/pdf", views.download_report_pdf, name="download_report_pdf"),
]