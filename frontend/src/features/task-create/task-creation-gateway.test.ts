import { describe, expect, it } from "vitest";

import {
  douyinSearchExposureDefinitionSchema,
  validateTaskCreationInput,
} from "./task-creation-gateway";

const definition = {
  template: "douyin.search_exposure.v1" as const,
  searchKeyword: "新能源汽车",
  action: "browse" as const,
  messageTemplate: null,
  targetLimit: 10,
  minimumIntervalSeconds: 30,
  maximumIntervalSeconds: 90,
  previewRequired: true as const,
  finalConfirmationRequired: true as const,
};

describe("Douyin discovery input policy", () => {
  it("counts Unicode code points exactly like the server", () => {
    const maximum = { ...definition, searchKeyword: "😀".repeat(80) };

    expect(validateTaskCreationInput(maximum, "task:unicode:80")).toEqual(maximum);
    expect(
      douyinSearchExposureDefinitionSchema.safeParse({
        ...definition,
        searchKeyword: "😀".repeat(81),
      }).success,
    ).toBe(false);
  });

  it.each([
    { searchKeyword: "" },
    { searchKeyword: " leading" },
    { searchKeyword: "trailing\u00a0" },
    { searchKeyword: "line\nbreak" },
    { searchKeyword: "control\u0085character" },
    { searchKeyword: "visual\u202etrap" },
    { targetLimit: 0 },
    { targetLimit: 101 },
  ])("rejects the shared whitespace, control, and limit matrix: %o", (change) => {
    expect(
      douyinSearchExposureDefinitionSchema.safeParse({ ...definition, ...change }).success,
    ).toBe(false);
  });
});
