import tw_ci as ci
from tw_ci.options.build_options import BuildOptionsWithBuilderAndChecks
from tw_ci.internal_utils import BuildTagConverter

install_name = ""
production_namespace = "<class 'jinja2.utils.Namespace'>"
feature_namespace = "staging"

_secrets = {"package_registry_token": "GITLAB_PACKAGE_REGISTRY_TOKEN"}
build_options = BuildOptionsWithBuilderAndChecks(
    builder_secrets=_secrets,
    app_secrets=_secrets,
    checks_secrets=_secrets,
)
check_reference = build_options.checks_ref
app_reference = build_options.app_ref

### Jobs ###


def job_e2e_test():
    return (
        ci.job_python_e2e_tests(
            "registry.gitlab.com/techwolfbe/infrastructure/base-images/python-e2e"
        )
        .with_needs("deploy")
        .with_variables(
            {
                "MS_URL": "https://$INGRESS_URL",
                "HTTP_AUTH_USER": "$GL_INGRESS_USER",
                "HTTP_AUTH_PASSWORD": "$GL_INGRESS_PASSWORD",
            }
        )
        .with_tags("default")
    )


def job_unit_tests():
    return (
        ci.job_python_unit_tests(
            image=check_reference.image,
            source_folder="src",
        )
        .with_needs("build-app")
        .with_pre_script(
            "python -m ensurepip",
            "pip install -r requirements/additional_requirements_test.txt",
        )
    )


def job_deploy(environment: str, namespace: str, install_name: str):
    return ci.job_deploy_local_chart(
        environment=environment,
        install_name=install_name,
        namespace=namespace,
        timeout_seconds=120,
    ).with_needs("build-app")


def job_release_pipeline():
    return ci.job_release_pipeline(release_pipeline).with_no_needs()


def job_teardown_pipeline():
    return (
        ci.job_trigger_pipeline("teardown", teardown_pipeline)
        .with_stage("teardown")
        .with_no_needs()
        .with_rules()
    )


def environment_feature(action: str):
    return ci.CIEnvironment(
        "feature/$CI_COMMIT_REF_SLUG",
        action=action,
    )


def environment_feature_deploy():
    return ci.CIEnvironment(
        "feature/$CI_COMMIT_REF_SLUG",
        action="start",
        on_stop="teardown",
        auto_stop_in="2 hours",
    )


def environment_staging(action: str):
    return ci.CIEnvironment(
        "staging",
        action=action,
    )


### Pipelines

# Release
ref = BuildTagConverter(app_reference.image)
release_pipeline = ci.CIPipeline(
    stages=["prepare", "release", "migrations", "deploy"],
    variables={
        "IMAGE_BASE": ref.release_image.split(":")[0],
        "IMAGE_TAG": ref.release_tag,
    },
    jobs=[
        ci.job_prepare_release(),
        ci.job_retag_image(app_reference.image),
        ci.job_terragrunt_apply(folder="infrastructure/environments/environment-eu"),
        ci.job_terragrunt_apply(folder="infrastructure/environments/environment-us"),
    ],
)

# Teardown
teardown_pipeline = ci.CIPipeline(
    stages=["teardown"],
    jobs=[
        ci.job_teardown_deploy(
            namespace=feature_namespace,
            install_name=f"{install_name}-$CI_COMMIT_REF_SLUG",
        ),
    ],
)

# Feature & Staging
stages = [
    "build",
    "test",
    "checks",
    "migrations",
    "deploy",
    "test-deploy",
    "teardown",
    "release",
]


feature_pipeline = ci.CIPipeline(
    stages=stages,
    variables={
        "INGRESS_URL": f"{install_name}-$CI_COMMIT_REF_SLUG.pikachu.skillengine.be",
        "IMAGE_BASE": app_reference.image.split(":")[0],
        "IMAGE_TAG": "$CI_COMMIT_REF_SLUG",
    },
    jobs=[
        *ci.combo_docker_build(build_options),
        job_unit_tests(),
        *ci.combo_devops_checks(check_reference),
        *ci.combo_python_checks(check_reference),
        ci.job_terragrunt_apply(folder="infrastructure/environments/environment-eu"),
        job_e2e_test().with_environment(environment_feature("verify")),
        job_teardown_pipeline().with_environment(environment_feature("stop")),
    ],
)

staging_pipeline = ci.CIPipeline(
    stages=stages,
    variables={
        "INGRESS_URL": f"{install_name}.pikachu.skillengine.be",
        "IMAGE_BASE": app_reference.image.split(":")[0],
        "IMAGE_TAG": "$CI_COMMIT_REF_SLUG",
    },
    jobs=[
        *ci.combo_docker_build(build_options),
        job_unit_tests(),
        *ci.combo_devops_checks(check_reference),
        *ci.combo_python_checks(check_reference),
        job_deploy(
            environment="staging",
            namespace=production_namespace,
            install_name=install_name,
        ).with_environment(environment_staging("start")),
        job_e2e_test().with_environment(environment_staging("verify")),
        job_release_pipeline().with_inherit(ci.CIInherit(variables=False)),
    ],
)

### Generating
pipeline = ci.FeatureStagingPipeline(
    on_feature=feature_pipeline,
    on_staging=staging_pipeline,
)

pipeline.generate()
ci.lint()
ci.lint("gitlab-ci/feature.yaml")
ci.lint("gitlab-ci/staging.yaml")
ci.lint("gitlab-ci/teardown.yaml")
ci.lint("gitlab-ci/release.yaml")
