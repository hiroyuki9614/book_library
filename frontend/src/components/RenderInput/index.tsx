import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { zodResolver } from "@hookform/resolvers/zod";
import { Controller, useForm } from "react-hook-form";
import type { ControllerRenderProps, ControllerFieldState, UseFormReturn } from "react-hook-form";
import * as z from "zod";

type FormValues = {
  title: string;
  description: string;
};

type RenderInputProps = {
  form: UseFormReturn<FormValues>;
  formItem: {
    label: string;
    placeholder: string;
    type: string;
    name: keyof FormValues;
    autoComplete: string;
  };
};

type FormItemType = {
  field: ControllerRenderProps<FormValues, keyof FormValues>;
  fieldState: ControllerFieldState;
  formItem: RenderInputProps["formItem"];
};

const inputComponentTypes = ["text", "password", "email", "number", "search", "tel", "url"];

function RenderInput({ form, formItem }: RenderInputProps) {
  const formItemType = ({ field, fieldState, formItem }: FormItemType) => {
    switch (formItem.type) {
      case inputComponentTypes.includes(formItem.type) ? formItem.type : "text":
        return <Input {...field} type={formItem.type} id={formItem.name} className="resize-none" aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete} />;
      case "textarea":
        return <textarea {...field} id={formItem.name} className="resize-none" aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete} />;
      case "select":
        return (
          <select {...field} id={formItem.name} className="resize-none" aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete}>
            {/* Options should be rendered here */}
          </select>
        );
      case "checkbox":
        return <input {...field} type="checkbox" id={formItem.name} className="resize-none" aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete} />;
      case "radio":
        return <input {...field} type="radio" id={formItem.name} className="resize-none" aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete} />;
      default:
        return null;
    }
  };

  return (
    <>
      {/* テキストインプット */}

      <Controller
        name={formItem.name}
        control={form.control}
        render={({ field, fieldState }) => (
          <Field data-invalid={fieldState.invalid}>
            <FieldLabel htmlFor={formItem.name}>{formItem.label}</FieldLabel>
            {formItemType({ field, fieldState, formItem })}
            <p>{fieldState.invalid && <FieldError errors={[fieldState.error]} />}</p>
          </Field>
        )}
      />
    </>
  );
}

export default RenderInput;
