EXAMPLE_USER_CODE = """
import { Builtin } from "floww";

const builtin = new Builtin()

builtin.triggers.onCron({
  expression: "*/10 * * * *",
  handler: async (ctx, event) => {
    console.log("Hello, world!");
  },
});
"""
