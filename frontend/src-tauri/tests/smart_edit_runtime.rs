use automation_tool_desktop_lib::smart_edit_runtime::{
    SmartEditGenerationMode, SmartEditGenerationRequest, SmartEditRuntime,
    SmartEditRuntimeErrorCode,
};

#[test]
fn smart_edit_runtime_exposes_one_generation_boundary_for_draft_and_render() {
    let request = SmartEditGenerationRequest::new(
        "623e4567-e89b-42d3-a456-426614174105",
        "把发布会开场剪成一条节奏明快的短片",
        false,
        SmartEditGenerationMode::Draft,
    )
    .expect("valid smart-edit request");

    assert_eq!(request.project_id(), "623e4567-e89b-42d3-a456-426614174105");
    assert_eq!(request.mode(), SmartEditGenerationMode::Draft);
    assert_eq!(
        format!("{request:?}"),
        "SmartEditGenerationRequest(<redacted>)"
    );
    let render = SmartEditGenerationRequest::new(
        "623e4567-e89b-42d3-a456-426614174105",
        "把发布会开场剪成一条节奏明快的短片",
        true,
        SmartEditGenerationMode::Render,
    )
    .expect("the one-click mode uses the same generation request");
    assert_eq!(render.mode(), SmartEditGenerationMode::Render);
    assert!(!format!("{render:?}").contains("发布会"));
}

#[test]
fn smart_edit_request_and_generation_lookup_fail_with_fixed_path_free_errors() {
    for (project_id, prompt) in [
        ("private-project", "有效提示"),
        ("623e4567-e89b-42d3-a456-426614174105", " 前后有空格 "),
        ("623e4567-e89b-42d3-a456-426614174105", "含有\0控制符"),
    ] {
        let error = SmartEditGenerationRequest::new(
            project_id,
            prompt,
            false,
            SmartEditGenerationMode::Draft,
        )
        .expect_err("invalid request must fail before any Worker or model call");
        assert_eq!(error.code(), SmartEditRuntimeErrorCode::InvalidRequest);
        assert!(!format!("{error:?}").contains(project_id));
        assert!(!format!("{error:?}").contains(prompt));
    }

    let runtime = SmartEditRuntime::new();
    let missing = "123e4567-e89b-42d3-a456-426614174100";
    assert_eq!(
        runtime
            .snapshot(missing)
            .expect_err("unknown generation is not inferred")
            .code(),
        SmartEditRuntimeErrorCode::GenerationNotFound,
    );
    assert_eq!(
        runtime
            .cancel(missing)
            .expect_err("unknown generation is not cancellable")
            .code(),
        SmartEditRuntimeErrorCode::GenerationNotFound,
    );
}
