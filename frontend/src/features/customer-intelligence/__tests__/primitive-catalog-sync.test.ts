import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { primitiveLabels } from "../primitive-catalog";

// Vitest runs with the frontend/ directory as its root; contracts/ sits at the
// repository root, one level above.
const contractPath = resolve(process.cwd(), "../contracts/primitive-catalog.json");

interface PrimitiveContractEntry {
  name: string;
  dependency_arity: { minimum: number; maximum: number };
  required_metric_keys: string[];
  dynamic_metric: boolean;
  description_ko: string | null;
  objective_en: string;
}

interface PrimitiveContract {
  schema_version: number;
  primitives: PrimitiveContractEntry[];
}

function loadContract(): PrimitiveContract {
  const raw = readFileSync(contractPath, "utf-8");
  return JSON.parse(raw) as PrimitiveContract;
}

describe("primitive catalog contract sync", () => {
  it("labels exactly the primitives declared in contracts/primitive-catalog.json", () => {
    const contract = loadContract();
    expect(contract.schema_version).toBe(1);
    const contractNames = contract.primitives.map((primitive) => primitive.name);
    expect(Object.keys(primitiveLabels)).toEqual(contractNames);
  });
});
