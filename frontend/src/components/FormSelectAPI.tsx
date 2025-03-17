import React, { useContext, useState } from "react";
import FormSelect from "./FormSelect.tsx";
import { useQuery, useMutation } from "@tanstack/react-query";
import AuthContext from "../context/AuthContext";
import { getData, postData } from "../utils/FetchFunctions";

interface Props {
	name: string;
	id: string;
	value?: string | string[] | null;
	label: string;
	choices: [];
	apiSearchKey?: string | null;
	isSearchable?: boolean;
	isClearable?: boolean;
	apiURL: string;
	valueKey: string;
	labelKey: string;
	multiple?: boolean;
	creatable?: boolean;
	handleChange?: (e) => void;
	valid?: boolean;
}

const FormSelectAPI = ({
	name,
	id,
	value = null,
	label,
	choices = [],
	apiSearchKey = null,
	isSearchable = true,
	isClearable = true,
	apiURL,
	valueKey,
	labelKey,
	multiple = false,
	creatable = false,
	handleChange = () => {},
	valid = true,
}: Props) => {
	const { authTokens } = useContext(AuthContext);
	const [searchString, setSearchString] = useState(value);
	const getDataFunc = async () => {
		if (apiURL === "") {
			return [];
		}
		let fullAPIURL;

		if (apiSearchKey !== null && searchString !== null) {
			fullAPIURL = `${apiURL}?${apiSearchKey}=${searchString}`;
		} else {
			fullAPIURL = apiURL;
		}
		let response_json = await getData(fullAPIURL, authTokens.access);

		if ("results" in response_json) {
			response_json = response_json["results"];
		}
		console.log(response_json);

		let newOptions = response_json.map((x) => {
			return { value: x[valueKey], label: x[labelKey] };
		});
		let allOptions = choices.concat(newOptions);
		console.log(allOptions);
		return allOptions;
	};

	const { data, isLoading, refetch } = useQuery({
		queryKey: [apiURL],
		queryFn: getDataFunc,
	});

	const handleCreate = async (newvalue: string) => {
		let results = await doCreate.mutateAsync(newvalue);
		let newoption = { label: results[labelKey], value: results[valueKey] };
		return newoption;
	};

	const doCreate = useMutation({
		mutationFn: (inputValue: any) => newPOST(inputValue),
	});

	const onInput = function (newValue) {
		setSearchString(newValue);
		refetch();
	};

	const newPOST = async (inputValue) => {
		let newData = {};
		newData[labelKey] = inputValue;
		let response_json = await postData(apiURL, authTokens.access, newData);
		//console.log(response_json);
		refetch();
		return response_json;
	};

	//console.log(value);

	return (
		<FormSelect
			key={id}
			name={name}
			id={id}
			value={isLoading ? null : value}
			label={label}
			choices={data}
			isLoading={isLoading}
			isSearchable={isSearchable}
			multiple={multiple}
			creatable={creatable}
			handleCreate={handleCreate}
			handleChange={handleChange}
			handleInput={onInput}
			isClearable={isClearable}
			valid={valid}
		/>
	);
};

export default FormSelectAPI;
