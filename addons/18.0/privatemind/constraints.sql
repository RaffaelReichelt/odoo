ALTER TABLE public.ir_module_module DROP CONSTRAINT IF EXISTS ir_module_module_name_uniq;
ALTER TABLE public.ir_module_module ADD CONSTRAINT ir_module_module_name_uniq UNIQUE (name)
