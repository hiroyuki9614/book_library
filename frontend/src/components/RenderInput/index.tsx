import { Input } from '@/components/ui/input';
import { Field, FieldError, FieldLabel } from '@/components/ui/field';
import { Controller } from 'react-hook-form';
import type { ControllerFieldState, ControllerRenderProps, FieldValues, Path, UseFormReturn } from 'react-hook-form';

type RenderInputProps<T extends FieldValues> = {
	form: UseFormReturn<T>;
	formItem: {
		label: string;
		placeholder: string;
		type: string;
		name: Path<T>;
		autoComplete: string;
	};
};

type FormItemType<T extends FieldValues> = {
	field: ControllerRenderProps<T, Path<T>>;
	fieldState: ControllerFieldState;
	formItem: RenderInputProps<T>['formItem'];
};

const inputComponentTypes = ['text', 'password', 'email', 'number', 'search', 'tel', 'url'];

function RenderInput<T extends FieldValues>({ form, formItem }: RenderInputProps<T>) {
	const formItemType = ({ field, fieldState, formItem }: FormItemType<T>) => {
		switch (formItem.type) {
			case inputComponentTypes.includes(formItem.type) ? formItem.type : 'text':
				return <Input {...field} type={formItem.type} id={formItem.name} className='resize-none' aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete} />;
			case 'textarea':
				return <textarea {...field} id={formItem.name} className='resize-none' aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete} />;
			case 'select':
				return (
					<select {...field} id={formItem.name} className='resize-none' aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete}>
						{/* Options should be rendered here */}
					</select>
				);
			case 'checkbox':
				return <input {...field} type='checkbox' id={formItem.name} className='resize-none' aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete} />;
			case 'radio':
				return <input {...field} type='radio' id={formItem.name} className='resize-none' aria-invalid={fieldState.invalid} autoComplete={formItem.autoComplete} />;
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
