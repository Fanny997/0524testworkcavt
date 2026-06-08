from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CustomOperationViewSet, serve_result_file

router = DefaultRouter(trailing_slash=False)
router.register("definitions", CustomOperationViewSet, basename="custom_operation")

urlpatterns = [
    # 自定义操作原有接口：
    #   GET/POST /api/custom-operations/definitions
    #   GET/PATCH/DELETE /api/custom-operations/definitions/{id}
    #   POST /api/custom-operations/definitions/{id}/execute
    path("api/custom-operations/", include(router.urls)),

    # 结果文件访问接口：
    #   GET /api/results/{run_id}/{filename}
    path("api/results/<int:run_id>/<path:filename>", serve_result_file, name="custom_operation-result"),
]
