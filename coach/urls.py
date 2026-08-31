from django.contrib.auth import views as auth_views
from django.urls import path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.profile_edit_view, name="profile_edit"),
    path("candidates/", views.candidate_management_view, name="candidate_management"),
    path("candidates/add/", views.candidate_add_view, name="candidate_add"),
    path("candidates/<uuid:candidate_id>/", views.candidate_detail_view, name="candidate_detail"),
    path("candidates/<uuid:candidate_id>/edit/", views.candidate_edit_view, name="candidate_edit"),
    path("candidates/<uuid:candidate_id>/delete/", views.candidate_delete_view, name="candidate_delete"),
    path("interview", views.interview_page, name="interview_page"),
    path("report/<str:session_id>", views.report_page, name="report_page"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("accounts/register/", views.register_view, name="register"),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html", redirect_authenticated_user=True), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="home"), name="logout"),
    path("api/start-session", views.start_session, name="start_session"),
    path("api/upload-response", views.upload_response, name="upload_response"),
    path("api/finish-interview", views.finish_interview, name="finish_interview"),
    path("api/report/<str:session_id>", views.get_report, name="get_report"),
    path("api/report/<str:session_id>/pdf", views.download_report_pdf, name="download_report_pdf"),
]